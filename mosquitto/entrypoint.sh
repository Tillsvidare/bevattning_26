#!/bin/sh
# Ersätter eclipse-mosquitto-imagens entrypoint i produktion.
#
# 1. Väntar tills Caddy skaffat Lets Encrypt-cert för $MQTT_DOMAIN och
#    kopierar det till en mosquitto-läsbar plats (Caddys nyckel är 600 root).
# 2. Ser till att password_file finns (backend skriver om den vid start).
# 3. Startar mosquitto och SIGHUP:ar den när passwd-filen eller certet
#    ändras (poll var 3:e sekund) — så plockas nya enhetskonton och
#    förnyade cert upp utan omstart.
set -eu

CERT_SRC="/caddy-data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/${MQTT_DOMAIN}"
CERT_DST=/mosquitto/certs
PASSWD=/mqtt-auth/passwd

mkdir -p "$CERT_DST"
[ -f "$PASSWD" ] || : > "$PASSWD"
chmod 644 "$PASSWD"

copy_cert() {
    cp "$CERT_SRC/${MQTT_DOMAIN}.crt" "$CERT_DST/cert.pem"
    cp "$CERT_SRC/${MQTT_DOMAIN}.key" "$CERT_DST/key.pem"
    chown mosquitto:mosquitto "$CERT_DST"/*.pem
    chmod 600 "$CERT_DST/key.pem"
}

echo "entrypoint: väntar på Caddys cert för ${MQTT_DOMAIN} ..."
until [ -f "$CERT_SRC/${MQTT_DOMAIN}.crt" ]; do sleep 3; done
copy_cert
echo "entrypoint: cert på plats, startar mosquitto"

mosquitto -c /mosquitto/config/mosquitto.conf &
MOSQ_PID=$!

mtime() { stat -c %Y "$1" 2>/dev/null || echo 0; }

last="$(mtime "$PASSWD")-$(mtime "$CERT_SRC/${MQTT_DOMAIN}.crt")"
while kill -0 "$MOSQ_PID" 2>/dev/null; do
    sleep 3
    now="$(mtime "$PASSWD")-$(mtime "$CERT_SRC/${MQTT_DOMAIN}.crt")"
    if [ "$now" != "$last" ]; then
        last="$now"
        copy_cert
        kill -HUP "$MOSQ_PID"
        echo "entrypoint: SIGHUP (passwd/cert ändrad)"
    fi
done
wait "$MOSQ_PID"
