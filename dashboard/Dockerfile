# Production dashboard image. Build from the dashboard directory:
#   docker build -t urumi-dashboard:local dashboard
# The Helm chart mounts the Nginx site file. This image only contains the static build.
FROM node:22-alpine AS build

WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY --from=build /src/dist /usr/share/nginx/html
