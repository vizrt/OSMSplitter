[![Build Status](https://travis-ci.com/vizrt/OSMSplitter.svg?branch=master)](https://travis-ci.com/vizrt/OSMSplitter)

# VizOSMSplitter
Splits OSM PBF files into subregions

Recommended requirements:   
-Python 3.6+   
-Docker (not necessary, but makes dependency management more convenient)

Download planet-latest.osm.pbf from https://planet.openstreetmap.org/ and place it in a new folder.   
Navigate to the newly created folder and run   
"docker run -it --mount src="$(pwd)",dst=/app/planet,type=bind -i -t hauhav/osmsplitter"   
The output files should appear in the newly created folder.


for options run python .\countrymaker.py -h
