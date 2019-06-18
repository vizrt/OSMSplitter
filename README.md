[![Build Status](https://travis-ci.com/vizrt/OSMSplitter.svg?branch=master)](https://travis-ci.com/vizrt/OSMSplitter)

# VizOSMSplitter
Splits OSM PBF files into smaller files, using subregions.
The user can provide max-filesize of output files, then the script will try to output folders containg files of the given size for the input pbf file.
It's also possible to create shapefiles using this script, where the user can provide a list of desired output shapefile names, along with the sqlite queries necessary to extract the content from the PBFs. 

Recommended requirements:   
-Python 3.6+   
-Docker (not necessary, but makes dependency management more convenient)

Download planet-latest.osm.pbf from https://planet.openstreetmap.org/ and place it in a new folder.   
Navigate to the newly created folder and run   
"docker run -it --mount src="$(pwd)",dst=/app/planet,type=bind -i -t hauhav/osmsplitter"   
The output files should appear in the newly created folder.


for options run python .\countrymaker.py -h
