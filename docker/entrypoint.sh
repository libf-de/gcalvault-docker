#!/bin/bash

if [ "$1" == "-a" ]; then
    echo "Initiating image..."
    /usr/local/bin/gcalvault -a
    exit 0
fi

if [ ! -f "/root/.gcalvault/.user" ]; then
    echo "No authentication data found, please initiate this image first!"
    exit 1
fi

echo "Creating cronjob..."

export > /env

EXECAT="${EXECAT:-0 3 * * *}"
echo "$EXECAT /bin/bash /cron.sh" >> /var/spool/cron/crontabs/root

echo "Syncing now..."
/usr/local/bin/gcalvault sync

echo "Starting cron..."
if [ "${DEBUG:-false}" == "true" ]; then
  crond -f -l 2 -L /dev/stdout
else
  crond -f
fi