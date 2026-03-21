# aave-tracker

### What does this thing do

This app makes requests to the Ethereum blockchain to fetch upcoming liquidations via the Aave protocol.
It stores the data into a PostgreSQL database so the frontend (separate repo) doesn't get
bottlenecked by the blockchain. A GraphQL endpoint is exposed for the client to query.

### Tech Stack

- **Django** - Web framework
- **Celery** - Async task queue for fetching blockchain data
- **Redis** - Message broker for Celery
- **PostgreSQL** - Database
- **Flower** - Celery task monitoring
- **Web3.py** - Ethereum blockchain interaction
- **Graphene** - GraphQL API

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- An [Etherscan API key](https://etherscan.io/apis)

### Setup

1. **Create the environment file**

   Create the file `.env/.dev-sample`:

   ```bash
   mkdir -p .env
   ```

   Then add the following contents to `.env/.dev-sample`:

   ```
   DEBUG=1
   SECRET_KEY=arandomencryptedkey
   DJANGO_ALLOWED_HOSTS=*

   SQL_ENGINE=django.db.backends.postgresql
   SQL_DATABASE=hello_django
   SQL_USER=hello_django
   SQL_PASSWORD=hello_django
   SQL_HOST=db
   SQL_PORT=5432

   CELERY_BROKER=redis://redis:6379/0
   CELERY_BACKEND=redis://redis:6379/0

   ETHERSCAN_API_KEY=<your_etherscan_api_key>
   ```

2. **Build and start the containers**

   ```bash
   docker-compose build
   docker-compose up -d
   ```

3. **Run database migrations**

   ```bash
   docker-compose exec web python manage.py migrate
   ```

4. **Create a superuser (optional)**

   ```bash
   docker-compose exec web python manage.py createsuperuser
   ```

### Services

Once running, the following services are available:

| Service       | URL                        |
|---------------|----------------------------|
| Web app       | http://localhost:8010       |
| GraphQL API   | http://localhost:8010/graphql/ |
| Django Admin  | http://localhost:8010/admin/   |
| Flower        | http://localhost:5557       |

The GraphQL endpoint has GraphiQL enabled, so you can explore the schema interactively in the browser.

### Useful Commands

```bash
# View logs
docker-compose logs -f

# Stop all services
docker-compose down

# Rebuild after dependency changes
docker-compose build --no-cache
docker-compose up -d
```
