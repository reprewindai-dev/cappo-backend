set -e
SRC=/data/coolify/applications/cappo-backend
APP=/data/coolify/applications/yen2fecq8burtsgqrm2b988e

cd $SRC
git fetch origin main
git checkout main
git pull --ff-only origin main
SHA=$(git rev-parse HEAD)
TAG=yen2fecq8burtsgqrm2b988e:${SHA}

docker build -t $TAG .

cd $APP
cp docker-compose.yaml docker-compose.yaml.pre-${SHA}
sed -i "s#^\([[:space:]]*\)image: .*#\1image: '$TAG'#" docker-compose.yaml
docker compose -f docker-compose.yaml config >/tmp/cappo-compose-config.out
docker compose -f docker-compose.yaml up -d --force-recreate

# Wait for container to start
sleep 5
CONTAINER_ID=$(docker ps -q -f "name=yen2fecq8burtsgqrm2b988e" | head -n 1)
docker exec $CONTAINER_ID alembic upgrade head

echo "Deployment complete for cappo-backend"
