FROM nginx:stable-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html style.css app.js favicon.svg /usr/share/nginx/html/
COPY vendor/ /usr/share/nginx/html/vendor/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
