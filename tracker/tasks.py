from celery import shared_task
from web3 import Web3

endpoint = 'https://goerli.infura.io/v3/67949a338f7a4b8a8c3be4fddf71f95d'

@shared_task
def foo():
    w3 = Web3(Web3.HTTPProvider(endpoint))

    #contract_abi =  # contract ABI goes here
    #contract_address =  # contract address goes here
    #aave_v3_contract = web3.eth.contract(address=contract_address, abi=contract_abi)

    # https://arbitrum-goerli.infura.io/v3/67949a338f7a4b8a8c3be4fddf71f95d
    pass