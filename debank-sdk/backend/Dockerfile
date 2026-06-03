FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package.json package-lock.json tsconfig.json ./
COPY dist ./dist
COPY src ./src
COPY assets ./assets
COPY backend/package.json backend/tsconfig.json ./backend/

WORKDIR /app/backend
RUN npm install

COPY backend/src ./src
RUN npm run build

FROM node:22-bookworm-slim

WORKDIR /app/backend
ENV NODE_NO_WARNINGS=1

COPY --from=build /app/package.json /app/package.json
COPY --from=build /app/package-lock.json /app/package-lock.json
COPY --from=build /app/dist /app/dist
COPY --from=build /app/src /app/src
COPY --from=build /app/assets /app/assets
COPY --from=build /app/tsconfig.json /app/tsconfig.json
COPY --from=build /app/backend/package.json ./package.json
COPY --from=build /app/backend/node_modules ./node_modules
COPY --from=build /app/backend/dist ./dist

CMD ["npm", "run", "start"]
