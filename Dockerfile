FROM python:3.9-alpine

RUN apk add --no-cache bash git openssh

COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/cron.sh /cron.sh

COPY dist/gcalvault-latest.tar.gz /usr/local/src/

RUN cd /usr/local/src \
    && pip install gcalvault-latest.tar.gz[test] \
    && mkdir -p /root/gcalvault

WORKDIR /root/gcalvault
ENV IS_DOCKER Yes
RUN chmod a+x /entrypoint.sh
ENTRYPOINT [ "/entrypoint.sh" ]
#ENTRYPOINT [ "gcalvault" ]
