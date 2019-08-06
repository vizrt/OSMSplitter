from urllib.parse import urlencode
import argparse
import requests
import xml.etree.ElementTree as ET
import time
import math
from pathlib import Path
from psutil import virtual_memory
import json
import os
import sys
import re
import subprocess
import shutil

overpassurl = "https://overpass-api.de/api/interpreter"
countryosmfile = Path("countries.osm")
osmconffile = Path("osmconf.ini")
relationsmapfile = Path('mapping.json')
blacklistfile = Path('blacklist.txt')
coastlinefolder = Path('coastlines')
overpassthrottle = 60.0

basepaths = {
    'relation': Path('countryrels'),
    'extract': Path('extracts'),
    'shape': Path('shapefiles/World'),
    'cutout': Path('countrycutouts'),
    'csv': Path('csv'),
}

# Get xml containing all relation IDs of countries as root nodes
post_body = """<osm-script>
    <query type="relation">
        <has-kv k="admin_level" v="2"/>
        <has-kv k="boundary" v="administrative" />
        <has-kv k="type"  modv="not" v="multilinestring" />
    </query>
    <print/>
</osm-script>"""

full_region = """<osm-script>
    <union into="_">
        <id-query type="relation" ref="{refid}"/>
        <recurse from="_" into="_" type="down"/>
    </union>
    <print/>
</osm-script>"""

#Replace area-query ref with 3600000000 + superrelationID
sub_region_query = """<osm-script>
    <query into="_" type="relation">
        <has-kv k="admin_level" v=""/>
        <has-kv k="boundary" modv="" v="administrative"/>
        <has-kv k="type" modv="not" v="multilinestring"/>
        <area-query ref=""/>
    </query>
    <print/>
</osm-script>"""

# SQL Queries which extract relevant parts of a pbf into shapefiles, based on the grouping ESRIpackager expects
shapefilecategories = {
     "Admins.shp": "select geometry, osm_id, osm_way_id, admin_level as fclass, coalesce(name_en, int_name, name) as name from multipolygons where admin_level is not null and boundary='administrative'"
}

filename_invalid_characters = re.compile(r'[\x00-\x1f/<>:"\\|?*]')  # Invalid on Windows and/or Linux
filename_invalid = re.compile(r'^(?:CON|PRN|AUX|NUL|COM\d|LPT\d)(?=\.|$)', re.IGNORECASE)  # Invalid on Windows
filename_end_invalid = re.compile(r'[. ]$')  # Also invalid on Windows
path_without_ext = re.compile(r"\..*$")
osmium_error = re.compile(r"While reading file '(?P<filepath>.*?)':")
coastlines_error = re.compile(r'There were (?P<warnings>\d+) warnings\.\nThere were (?P<errors>\d+) errors\.')
osminfo_extent = re.compile(r'^Extent: \((?P<xmin>.*?), (?P<ymin>.*?)\) - \((?P<xmax>.*?), (?P<ymax>.*?)\)$', re.MULTILINE)

class RetryWithUpdatedBlacklist(RuntimeError):
    pass

class Multipath:
    @classmethod
    def shapefolders(class_):
        basepath = basepaths['shape']
        for path in basepath.glob('**/Admins.shp'):
            yield class_(path.relative_to(basepath).parent)
    @classmethod
    def cutoutfiles(class_):
        basepath = basepaths['cutout']
        for path in basepath.glob('**/*.osm.pbf'):
            rel_str = stripext(path.relative_to(basepath), '.osm.pbf')
            yield class_(Path(rel_str))
    def __init__(self, relpath):
        self.relpath = relpath
    def relation(self):
        return basepaths['relation'] / (str(self.relpath) + '.osm')
    def shapefolder(self):
        return basepaths['shape'] / self.relpath
    def adminshape(self):
        return self.shapefolder() / 'Admins.shp'
    def landshape(self):
        return self.shapefolder() / 'Land.shp'
    def oceanshape(self):
        return self.shapefolder() / 'Ocean.shp'
    def cutout(self):
        return basepaths['cutout'] / (str(self.relpath) + '.osm.pbf')
    def cutouthassubfolder(self):
        return (basepaths['cutout'] / str(self.relpath)).is_dir()
    def csv(self):
        return basepaths['csv'] / self.relpath
    def multipolygons(self):
        return self.csv() / 'multipolygons.csv'

def escape_file_name(name):
    result = filename_invalid_characters.sub('', name)
    if filename_invalid.match(result):
        result = f'mmm{result}'
    if filename_end_invalid.search(result):
        result = f'{result}pizza'
    return result

def ensure_dir(dirpath):
    if dirpath:
        if not dirpath.is_dir():
            print(f'Creating "{dirpath}"')
            os.makedirs(dirpath, exist_ok=True)
            return True
    return False

def slurp(fname):
    with open(fname, encoding='UTF-8') as fh:
        return fh.read()

# Get all top level relation nodes from overpass, based on body
def make_overpass_request(url, body):
    print(body)
    print(f'Making overpass request to {url}')
    for i in range(3):
        response = requests.post(url=overpassurl, data=body)
        if response.status_code != 429:  # 429: Too many requests
            break
        print(f'Too many requests; trying again in {overpassthrottle} seconds')
        time.sleep(overpassthrottle)
    response.raise_for_status()
    response.encoding = 'UTF-8'
    return response.text

def get_relations(url, body, cachefile):
    if cachefile.is_file():
        xml = slurp(cachefile)
    else:
        xml = make_overpass_request(url, body)
        ensure_dir(cachefile.parent)
        with open(cachefile, "w", encoding='UTF-8') as fh:
            fh.write(xml)
    document =  ET.fromstring(xml)
    allCountryRelations = document.findall("relation")
    print(f'Returning {len(allCountryRelations)} subregions for {cachefile}') 
    return allCountryRelations

# Adjust query to get subregions of region with specified id, and only get regions with specified admin_level
def get_subregion_relations(superRelationId, admin_level):
    my_sub_region = sub_region_query
    my_sub_region = my_sub_region.replace('<has-kv k="admin_level" v=""/>', f'<has-kv k="admin_level" v="{admin_level}"/>')
    my_sub_region = my_sub_region.replace('<area-query ref=""/>', f'<area-query ref="{3600000000 + int(superRelationId)}"/>')
    return my_sub_region

# Get an .osm xml file from overpass api, containing all relevant information to extract region using osmium
def get_full_region(id, filename, relationsFolder):
    filepath = relationsFolder / filename
    if filepath.exists():
        return
    os.makedirs(relationsFolder, exist_ok=True)
    print(f"Fetching full region for id: {id}, and writing to: {filepath}")
    return get_relations(overpassurl, full_region.format(refid=id), filepath)

def getTag(element, tag):
    targetElem = element.find(f"./tag/[@k='{tag}']")
    if targetElem is not None:
        return targetElem.get('v')
    return None

def get_full_regions_from_xml(source, relations, relationsFolder):
    mapping = []
    for relation in relations:
        id = relation.get("id")
        name = getTag(relation, 'name')
        englishName =  getTag(relation, 'name:en')
        if englishName or name:
            filename = escape_file_name((englishName or name) + '.osm')  # Prefer english name
        else:
            filename = None
        mapping.append([id, filename, name, englishName])
        if name is None:
            print(f"Warning: In XML from {source}, {id} has no name")
            continue
        get_full_region(id, filename, relationsFolder)
    os.makedirs(relationsFolder, exist_ok=True)
    mapfile = relationsFolder / relationsmapfile
    with open(mapfile, 'w') as fh:
        json.dump(mapping, fh)

def extract_required(fname):
    with open(fname) as fh:
        data = json.load(fh)
    basedir = Path(data['directory'])
    for extract in data['extracts']:
        outpath = basedir / extract['output']
        if not outpath.is_file():
            return True
    return False

def run_external_program(*args, onerr=None, quiet=False):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='UTF-8')
    if p.returncode or not quiet:
        print(p.stdout)
    if p.returncode:
        handled = False
        if onerr:
            handled = onerr(p.returncode, p.stdout)
        if not handled:
            sys.stdout.flush()
            raise RuntimeError(f'The external call "{" ".join(args)}" returned non-zero code {p.returncode}')
    return (p.returncode, p.stdout, p.stderr)

def osmium_extracts(extractsdir, osmfile):
    def errorHandler(code, stdout):
        m = osmium_error.search(stdout)
        if not m:
            print('Could not auto-add to blacklist because the osmium_error regex didn\'t match.')
            return False
        abspath =  Path(m['filepath'])
        relpath = abspath.relative_to(basepaths['relation'].resolve())
        with open(blacklistfile, 'a', encoding='UTF-8') as fh:
            fh.write(str(relpath) + '\n')
        print(f"NOTE: {relpath} auto-added to blacklist. Retrying operation.")
        raise RetryWithUpdatedBlacklist()
    # Extract countries
    if not extractsdir.is_dir():
        raise ValueError(f"{extractsdir} is not a directory.")
    if not osmfile.is_file():
        raise ValueError(f"{osmfile} does not exist.")
    extractfiles = sorted([entry for entry in extractsdir.iterdir() if entry.is_file()])
    for extractfile in extractfiles:
        if not extract_required(extractfile):
            continue
        print(f"Processing extract {extractfile.name}")
        run_external_program("osmium", "extract", "--overwrite", "--strategy", "simple", "-c", str(extractfile), str(osmfile), onerr=errorHandler)

def create_extraction_json(extractsdir, relationsfolder, cutoutsdir, blacklist):
    count = 0
    i = 0
    extractsfile = extractsdir / "extracts"
    data = {}

    os.makedirs(extractsdir, exist_ok=True)
    os.makedirs(cutoutsdir, exist_ok=True)
    mem = virtual_memory()
    twoGig = 2000000000 
    #The extract tool uses approximately 1-2gb per country
    batchsize = math.floor(mem.total/twoGig)
    print(f"batchsize is {batchsize}")

    mapfile = relationsfolder / relationsmapfile
    with open(mapfile, encoding='UTF-8') as fh:
        mapping = json.load(fh)

    # Clean up existing files because files containing only blacklisted items won't be overwritten.
    for fn in extractsdir.glob(f'extracts*.json'):
        os.remove(fn)

    # Create json files which are used to extract countries in batches, reducing time used in loading the planet.pbf file
    extractlist = []
    for id_, relationfile, name, name_en in mapping:
        if name is None:
            continue
        if blacklist:
            relpath = relationsfolder.relative_to(basepaths['relation']) / relationfile
            if str(relpath) in blacklist:
                print(f"'{str(relpath)}' is in blacklist, skipping.")
                continue
        extract = {}
        extract["output"] = f"{relationfile}.pbf"
        polygon = {}
        polygon["file_name"] = str((relationsfolder / relationfile).resolve())
        polygon["file_type"] = "osm"
        extract['polygon'] = polygon
        extractlist.append(extract)
        count += 1
        if count == batchsize:
            data['extracts'] = extractlist
            data['directory'] = str(cutoutsdir)
            with open(f"{extractsfile}{i}.json", "w") as json_file:
                json.dump(data, json_file)
            i += 1
            data = {}
            extractlist = []
            count = 0
    if len(extractlist) > 0:
        data['extracts'] = extractlist
        data['directory'] = str(cutoutsdir)
        data['strategy'] = 'simple'
        with open(f"{extractsfile}{i}.json", "w") as json_file:
            json.dump(data, json_file)

# Convert pbf to shapefiles
def toshapefile(input, output):
    if output.is_dir():
        return
    try:
        os.makedirs(output)
        for (category, query) in shapefilecategories.items():
            print(f"category: {category},  query: {query}, from input: {input} to output: {output}")
            run_external_program(
                "ogr2ogr",
                "-oo", f"CONFIG_FILE={osmconffile}",
                "-lco",
                "ENCODING=UTF-8",
                "-dialect", "SQLITE",
                "-overwrite",
                "-f", "ESRI Shapefile",
                f"{output}/{category}",
                input,
                "-progress",
                "-sql", query)
    except KeyboardInterrupt as e:
        os.unlink(output)
        raise e from None

def stripext(path, ext): 
    path = str(path)
    if path.endswith(ext):
        path = path[:-len(ext)]
    else:
        raise RuntimeError(f'Expected {ext} on {path}')
    return path

def getNameToIdMap(osmFile):
    et = ET.parse(osmFile)
    relations = et.findall("relation")
    nameToIDMap = dict()
    for relation in relations:
        id = relation.get("id")
        name = getTag(relation, 'name')
        englishName =  getTag(relation, 'name:en')
        if name is None:
            print(f"Warning: In {osmFile}, {id} ({englishName}) has no name")
            continue
        nameToIDMap[name] = id
    return nameToIDMap

def extract(extractfolder, relationsfolder, cutoutfolder, planetfile, blacklist):
    create_extraction_json(extractfolder, relationsfolder, cutoutfolder, blacklist)
    try:
        osmium_extracts(extractfolder, planetfile)
    except RetryWithUpdatedBlacklist:
        blacklist = set(slurp(blacklistfile).split('\n'))
        extract(extractfolder, relationsfolder, cutoutfolder, planetfile, blacklist)

def produce_country_pbfs(blacklist, planetfile):
    countryRelations = get_relations(overpassurl, post_body, countryosmfile)
    get_full_regions_from_xml(countryosmfile, countryRelations, basepaths['relation'])
    extract(basepaths['extract'], basepaths['relation'], basepaths['cutout'], planetfile, blacklist)

def get_region_name_from_relative_path(path):
    mapfile = basepaths['relation'] / path.parent / relationsmapfile
    expected_fn = stripext(path.name, '.pbf')
    with open(mapfile) as fh:
        mapping = json.load(fh)
    for id_, filename, name, name_en in mapping:
        if filename == expected_fn:
            return name
    raise KeyError(f"{expected_fn} not in {mapfile}")

def produce_region_pbf(regionpath, regionNameToIdMap, admin_level, blacklist, threshold):
    relpath_from_countrycutouts = regionpath.relative_to(basepaths['cutout'])
    relpath_dir_name = relpath_from_countrycutouts.parent
    regionfilename = stripext(regionpath.name, '.osm.pbf')
    regionname = get_region_name_from_relative_path(relpath_from_countrycutouts)
    region_relations_file = Path("relations") / relpath_dir_name / f'{regionfilename}.osm'
    regionRelationFolder = basepaths['relation'] / relpath_dir_name / regionfilename
    regionExtractFolder = basepaths['extract'] / relpath_dir_name / regionfilename
    region_cutouts_target_dir = basepaths['cutout'] / relpath_dir_name / regionfilename

    for i in range(admin_level, 8, 2):
        print(f"Attempting to retrieve regions for admin_level: {i}")
        subregion_query = get_subregion_relations(regionNameToIdMap[regionname], i)
        regionRelations = get_relations(overpassurl, subregion_query, region_relations_file)
        if len(regionRelations) > 0:
            print(f"Found regions for admin level: {i}")
            admin_level = i
            break
    else:
        return
    get_full_regions_from_xml(region_relations_file, regionRelations, regionRelationFolder)
    extract(regionExtractFolder, regionRelationFolder, region_cutouts_target_dir, regionpath, blacklist)
    for subregion_file in sorted([entry for entry in region_cutouts_target_dir.iterdir() if entry.is_file()]):
        if subregion_file.stat().st_size >= threshold:
            nameToIdMap = getNameToIdMap(region_relations_file)
            produce_region_pbf(subregion_file, nameToIdMap, admin_level + 2, blacklist, threshold)

def cutouts_to_shapefiles():
    for multipath in Multipath.cutoutfiles():
        if not multipath.cutouthassubfolder():
            toshapefile(multipath.cutout(), multipath.shapefolder())

def generate_coastlines(planetfile, targetdir):
    def handler(code, text):
        match = coastlines_error.search(text)
        if match and match['errors'] == '0':
            # osmcoastlines returns nonzero even if there were only warnings.
            return True
        return False
    print("Generating coastlines")
    ensure_dir(targetdir)
    both = targetdir / 'both.db'
    if not both.is_file():
        run_external_program('osmcoastline', '--output-polygons=both', '-o', str(both), str(planetfile), onerr=handler)
    land = targetdir / 'land.shp'
    water = targetdir / 'ocean.shp'
    if not land.is_file():
        run_external_program('ogr2ogr', '-f', 'ESRI Shapefile', str(land), str(both), 'land_polygons')
    if not water.is_file():
        run_external_program('ogr2ogr', '-f', 'ESRI Shapefile', str(water), str(both), 'water_polygons')
    return (land, water)

def get_extent(multipath, padding=None):
    retcode, stdout, stderr = run_external_program('ogrinfo', '-ro', '-so', str(multipath.adminshape()), 'Admins', quiet=True)
    match = osminfo_extent.search(stdout)
    if not match:
        raise RuntimeError(f'No extent for {shapefile}')
    xmin, ymin, xmax, ymax = match.group('xmin', 'ymin', 'xmax', 'ymax')
    if padding is not None:
        def pad(value, amount):
            return str(float(value) + amount)
        xmin = pad(xmin, -padding)
        ymin = pad(ymin, -padding)
        xmax = pad(xmax, padding)
        ymax = pad(ymax, padding)
    return xmin, ymin, xmax, ymax

def clip_region_coastlines(land, water):
    print("Clipping region coastlines")
    for multipath in Multipath.shapefolders():
        multipolygons = multipath.multipolygons()
        if not multipolygons.is_file():
            ensure_dir(multipath.csv().parent)
            run_external_program('ogr2ogr', '-f', 'CSV', str(multipath.csv()), str(multipath.relation()), '-lco', 'GEOMETRY=AS_WKT', quiet=True)
        outland = multipath.landshape()
        outwater = multipath.oceanshape()
        if not outland.is_file() or not outwater.is_file():
            print(str(multipath.relpath))
            extent = get_extent(multipath, 0.1)
        if not outland.is_file():
            run_external_program('ogr2ogr', '-skipfailures', '-spat', *extent, '-clipsrc', str(multipolygons), str(outland), str(land), quiet=True)
        if not outwater.is_file():
            run_external_program('ogr2ogr', '-skipfailures', '-spat', *extent, '-clipsrc', str(multipolygons), str(outwater), str(water), quiet=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Split the planet file')
    parser.add_argument('--planet-source', dest='sourcefile', default='planet-latest.osm.pbf', help='Path to the planet file you wish to split')
    parser.add_argument('--split-treshold', dest='threshold', type=int, default='150000000', help='Maximum size of pbf files (in bytes) after split')
    parser.add_argument('--overpass-server', dest='overpass', default='https://overpass-api.de/api/interpreter', help='Overpass server to use, should have as high usage limit as possible')
    parser.add_argument('--shapefile-queries', dest='shplist', default='shapefiles.json', help='a file containing all desired output shapefiles with sqlite queries, given in json format')
    parser.add_argument('--generate-shapefiles', dest='shapefile_creation', default='no', help='set to yes if you want to create shapefiles')
    parser.add_argument('--workingdir', dest='workingdir', default='', help='Path to the working directory where the planet file is found and the output should be')
    args = parser.parse_args()
    planetfile = Path(args.sourcefile)
    threshold = args.threshold
    overpassurl = args.overpass
    working_dir = Path(args.workingdir)
    osmconffile = osmconffile.resolve()
    os.chdir(working_dir)
    shapefile_queries = Path(args.shplist).read_text()
    shapefilecategories.update(json.loads(shapefile_queries))

    if not planetfile.is_file():
        raise RuntimeError(f"{planetfile.resolve()} is required. You may pass a different path as an argument to this script.")
    blacklist = blacklistfile.is_file() and set(slurp(blacklistfile).split('\n'))
    produce_country_pbfs(blacklist, planetfile)
    nameToIdMap = getNameToIdMap(countryosmfile)
    for relationfile in sorted([entry for entry in basepaths['cutout'].iterdir() if entry.is_file()]):
        produce_region_pbf(relationfile, nameToIdMap, 4, blacklist, threshold)

    if args.shapefile_creation != 'yes':
        quit()
    cutouts_to_shapefiles()
    (land, water) = generate_coastlines(planetfile, coastlinefolder)
    clip_region_coastlines(land, water)
