# aave-tracker

### What does this thing do

This app will make requests to the ethereum blockchain to fetch upcoming liquidations. 
Then it'll store the info into a postgres database so the frontend (different repo) doesn't need 
to get bottlenecked by the blockchain. 

We expose a graphql endpoint for the client to get info.


### How to make a graphql request

TBD

### How to start the project

Just run:

```commandline
docker-compose build
```

```commandline
docker-compose up -d
```