FROM node:22-alpine

WORKDIR /app
ENV BACKEND_URL=http://backend:8000

COPY package.json yarn.lock* ./
RUN yarn install --frozen-lockfile

COPY . .

EXPOSE 5173
CMD ["yarn", "dev"]
