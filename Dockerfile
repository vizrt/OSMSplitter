FROM ubuntu:18.04
RUN apt update && apt -y upgrade && apt -y install \
    python3 \
    python3-pip \
    gdal-bin \
    osmcoastline \
    osmium-tool
ENV PYTHONIOENCODING=utf-8
ENV LANG C.UTF-8
WORKDIR /app
COPY . /app
RUN pip3 install -r requirements.txt
ENTRYPOINT ["python3", "countrymaker.py", "--workingdir", "planet"]
