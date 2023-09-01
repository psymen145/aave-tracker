from celery import shared_task
from web3 import Web3
import os

endpoint = 'https://mainnet.infura.io/v3/67949a338f7a4b8a8c3be4fddf71f95d'

@shared_task
def foo():
    w3 = Web3(Web3.HTTPProvider(endpoint))

    #contract_abi =  # contract ABI goes here
    #contract_address =  # contract address goes here
    #aave_v3_contract = web3.eth.contract(address=contract_address, abi=contract_abi)

    # https://arbitrum-goerli.infura.io/v3/67949a338f7a4b8a8c3be4fddf71f95d
    pass


def get_logs(from_block, to_block, address, topic0, api_key):
    import requests
    import json

    url = "https://api.etherscan.io/api"
    querystring = {
        "module": "logs",
        "action": "getLogs",
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": address,
        "topic0": topic0,
        "apikey": api_key
    }

    response = requests.request("GET", url, params=querystring)

    data = json.loads(response.text)

    if not data.get("status"):
        print("cant get status")
        return

    result = data.get("result")
    return result


def test():
    from tracker.models import Contract

    w3 = Web3(Web3.HTTPProvider(endpoint))
    aave_v3_pool_model = Contract.objects.get(name="Pool", network_id=2)
    pool_contract_obj = w3.eth.contract(address=aave_v3_pool_model.address, abi=aave_v3_pool_model.abi)

    #print(dir(pool_contract_obj.functions))

    # when aave v3 was deployed on mainnet
    from_block = "16988017"
    to_block = "latest"

    # get the topic (which is the keccak hash of the signature function)

    event_signature = "Supply(address,address,address,uint256,uint16)"
    topic0 = Web3.keccak(text=event_signature).hex() # 0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61
    api_key = os.environ.get("ETHERSCAN_API_KEY")

    logs = get_logs(from_block, to_block, aave_v3_pool_model.address, topic0, api_key)

    all_address_interacted = set()
    for log in logs:
        topics = log.get("topics")
        if not topics:
            print("error, missing topics")
            continue

        # topics are a list of params used, first is always the event signature
        # addresses are padded
        user_addr = f"0x{topics[2][26:]}"

        all_address_interacted.add(user_addr)

    print(len(all_address_interacted))

    count = 0
    for user_addr in all_address_interacted:
        if count >= 20:
            break

        checksum_user_addr = Web3.to_checksum_address(user_addr)
        results = pool_contract_obj.functions.getUserAccountData(checksum_user_addr).call()

        total_collateral_eth = results[0] / 10 ** 18
        total_debt_eth = results[1] / 10 ** 18
        available_borrow_eth = results[2] / 10 ** 18
        current_liquidation_threshold = results[3] / 10 ** 4
        ltv = results[4] / 10 ** 4
        health_factor = results[5] / 10 ** 18

        print(user_addr, total_collateral_eth, total_debt_eth, available_borrow_eth, current_liquidation_threshold, ltv, health_factor)

        count += 1
