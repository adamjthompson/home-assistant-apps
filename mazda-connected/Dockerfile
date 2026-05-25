ARG BUILD_FROM
FROM node:18-alpine

# Set working directory
WORKDIR /app

# Copy package files and vendored node-mymazda source
COPY package.json ./
COPY node-mymazda/ ./node-mymazda/

# Install dependencies (node-mymazda resolved from local path)
RUN npm install --ignore-scripts

# Copy application files
COPY server.js ./

# Expose port
EXPOSE 3000

CMD ["node", "server.js"]
