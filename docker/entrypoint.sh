#!/bin/bash

if [ "$1" == "setup" ]; then
    echo "Initiating image..."
    /usr/local/bin/gcalvault setup
    exit 0
fi

if [ ! -f "/app/conf/config.json" ]; then
    echo "No configuration file found, please initiate this image first!"
    exit 1
fi

echo "Creating cronjob..."

export > /env

EXECAT="${EXECAT:-0 3 * * *}"
echo "$EXECAT /usr/local/bin/gcalvault" >> /var/spool/cron/crontabs/root

echo "Syncing now..."
/usr/local/bin/gcalvault sync

echo "Starting cron..."
if [ "${DEBUG:-false}" == "true" ]; then
  crond -f -l 2 -L /dev/stdout
else
  crond -f
fi