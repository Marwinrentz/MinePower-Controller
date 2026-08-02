# OPTIONAL: separates nginx-Frontend für Split-Deployments.
# Standard ist das All-in-One-Image (Dockerfile im Repo-Root) –
# dort serviert das Backend den Frontend-Build direkt mit.

# Build-Stage
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Serve-Stage (nginx, Multi-Arch)
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
