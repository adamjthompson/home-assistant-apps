ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Node.js 18
RUN apk add --no-cache nodejs npm

# Set working directory
WORKDIR /app

# Copy package files and vendored node-mymazda source
COPY package.json ./
COPY node-mymazda/ ./node-mymazda/

# Install dependencies (node-mymazda resolved from local path)
RUN npm install

# Copy application files
COPY server.js ./

# Expose port
EXPOSE 3000

CMD ["node", "server.js"]
