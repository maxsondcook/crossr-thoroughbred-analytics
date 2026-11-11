FROM node:20-alpine
WORKDIR /app
COPY web/package.json ./
RUN npm install
COPY web/ ./
ARG API_INTERNAL=http://api:8000
ENV API_INTERNAL=$API_INTERNAL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && chown -R node:node /app
# Serve as the image's unprivileged "node" user.
USER node
EXPOSE 3000
CMD ["npm", "start"]
