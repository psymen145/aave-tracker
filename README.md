# aave-tracker

### What does this thing do

This app will make requests to the ethereum blockchain to fetch upcoming liquidations. 
Then it'll store the info into a postgres database so the frontend (different repo) doesn't need 
to get bottlenecked by the blockchain. 

We expose a graphql endpoint for the client to get info.


### How to make a graphql request

TBD

### How to start the project

When developing local, make sure you have the following file in `/.env/.dev-sample`

```commandline
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

ETHERSCAN_API_KEY=etherscankey
```

Just run:

```commandline
docker-compose build
```

```commandline
docker-compose up -d
```