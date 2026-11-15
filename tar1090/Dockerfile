# Image tar1090 RadarDesk : retire les calques VFR/IFR ArcGIS inutilisés
# hors couverture US, qui génèrent des requêtes de tuiles en erreur.
FROM blackoutsecure/tar1090@sha256:8e3d544dea3dff50c61a4dd7fbee4a9428613686ba72ddaeda1a6da87137249c

COPY patch-layers.awk /tmp/patch-layers.awk
RUN awk -f /tmp/patch-layers.awk \
        /usr/local/share/tar1090/html/layers.js \
        > /tmp/layers.js \
    && mv /tmp/layers.js /usr/local/share/tar1090/html/layers.js \
    && rm /tmp/patch-layers.awk
