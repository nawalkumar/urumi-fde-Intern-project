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

# Application source identity for this final stage. unknown is not HEAD.
# These keys do not replace the nginx image's org.opencontainers.image.* labels.
ARG URUMI_SOURCE_REVISION=unknown
ARG URUMI_SOURCE_DIRTY=unknown
LABEL io.urumi.source.revision="${URUMI_SOURCE_REVISION}" \
      io.urumi.source.dirty="${URUMI_SOURCE_DIRTY}"
