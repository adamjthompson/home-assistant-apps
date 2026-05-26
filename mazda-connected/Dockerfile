ARG BUILD_FROM
FROM node:18-alpine

WORKDIR /app

COPY package.json ./
COPY node-mymazda/ ./node-mymazda/

RUN npm install --ignore-scripts

COPY server.js ./
COPY MazdaAuthClient.js ./

EXPOSE 3001

CMD ["node", "server.js"]