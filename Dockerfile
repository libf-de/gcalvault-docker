FROM python:3.9-alpine

RUN apk add --no-cache bash git

COPY dist/gcalvault-latest.tar.gz /usr/local/src/
COPY docker/entrypoint.sh /entrypoint.sh

RUN cd /usr/local/src \
    && pip install gcalvault-latest.tar.gz[test] \
    && mkdir -p /root/gcalvault

WORKDIR /root/gcalvault

RUN chmod a+x /entrypoint.sh
ENTRYPOINT [ "/entrypoint.sh" ]
#ENTRYPOINT [ "gcalvault" ]
