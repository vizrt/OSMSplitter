# VizOSMSplitter
Splits OSM PBF files into subregions

Recommended requirements:
-Python 3.6+
-Docker (not necessary, but makes dependency management more convenient)

navigate to this folder and run "docker build -t countrysplitter:v1 ." to create a docker image.
Download planet-latest.osm.pbf from https://planet.openstreetmap.org/ and place it in a new folder.
Navigate to the newly created folder and run
"docker run -it --mount src="$(pwd)",dst=/app/planet,type=bind -i -t countrysplitter:v1"
The output files should appear in the newly created folder.


options:
python .\countrymaker.py -h
usage: countrymaker.py [-h] [--planet-source SOURCEFILE]
                       [--split-treshold THRESHOLD]
                       [--overpass-server OVERPASS]
                       [--shapefile-queries SHPLIST]
                       [--generate-shapefiles SHAPEFILE_CREATION]
                       [--workingdir WORKINGDIR]

Split the planet file

optional arguments:
  -h, --help            show this help message and exit
  --planet-source SOURCEFILE
                        Path to the planet file you wish to split
  --split-treshold THRESHOLD
                        Maximum size of pbf files (in bytes) after split
  --overpass-server OVERPASS
                        Overpass server to use, should have as high usage
                        limit as possible
  --shapefile-queries SHPLIST
                        a file containing all desired output shapefiles with
                        sqlite queries, given in json format
  --generate-shapefiles SHAPEFILE_CREATION
                        set to yes if you want to create shapefiles
  --workingdir WORKINGDIR
                        Path to the working directory where the planet file is
                        found and the output should be