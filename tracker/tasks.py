from celery import shared_task
from web3 import Web3
import os

from tracker.models import Contract, Wallet, Position

endpoint = 'https://mainnet.infura.io/v3/67949a338f7a4b8a8c3be4fddf71f95d'
MAINNET_NETWORK_ID = 2

@shared_task
def pull_positions():
    # https://arbitrum-goerli.infura.io/v3/67949a338f7a4b8a8c3be4fddf71f95d

    w3 = Web3(Web3.HTTPProvider(endpoint))
    aave_v3_pool_model = Contract.objects.get(name="Pool", network_id=MAINNET_NETWORK_ID)
    pool_contract_obj = w3.eth.contract(address=aave_v3_pool_model.address, abi=aave_v3_pool_model.abi)

    # when aave v3 was deployed on mainnet
    from_block = "16988017"
    to_block = "latest"

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

    print(f"{len(all_address_interacted)} addresses supplied to with aave v3 eth mainnet")

    # create all wallets that we don't have stored yet
    db_addresses = Wallet.objects.filter(
        network_id=MAINNET_NETWORK_ID,
        deleted=False,
    ).values_list('address', flat=True)
    db_addresses_set = set(db_addresses)
    addresses_not_in_db = all_address_interacted - db_addresses_set
    wallets_to_create = [
        Wallet(
            address=address,
            network_id=MAINNET_NETWORK_ID,
        ) for address in addresses_not_in_db
    ]
    Wallet.objects.bulk_create(wallets_to_create)

    count = 0
    wallet_address_to_model_id = dict()
    all_wallet_models = Wallet.objects.filter(
        network_id=MAINNET_NETWORK_ID,
        address__in=all_address_interacted,
        deleted=False,
    )
    for wallet_model in all_wallet_models:
        # we expect each address to be unique, so don't worry about overriding
        wallet_address_to_model_id[wallet_model.address] = wallet_model.id

    for user_addr in all_address_interacted:

        checksum_user_addr = Web3.to_checksum_address(user_addr)
        results = pool_contract_obj.functions.getUserAccountData(checksum_user_addr).call()

        total_collateral_usd = results[0] / 10 ** 8
        total_debt_eth = results[1] / 10 ** 8
        available_borrow_eth = results[2] / 10 ** 18
        current_liquidation_threshold = results[3] / 10 ** 4
        ltv = results[4] / 10 ** 4
        health_factor = results[5] / 10 ** 18

        #print(user_addr, total_collateral_usd, total_debt_eth, available_borrow_eth, current_liquidation_threshold, ltv, health_factor)

        # not as efficient since it'll update one at a time, but there aren't that many positions right now
        Position.objects.update_or_create(
            contract_id=aave_v3_pool_model.id,
            deleted=False,
            network_id=MAINNET_NETWORK_ID,
            wallet_id=wallet_address_to_model_id[user_addr],
            defaults=dict(
                health_factor=health_factor,
                total_usd_collateral=total_collateral_usd
            ),
        )

        # TODO: add some logging
        count += 1

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
    """
    This task should call Etherscan's api and gets the latest info for all users that "Supplied" to aave

    This will update mostly all rows in the database. We need to do this
    """
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

    # create all wallets that we don't have stored yet
    db_addresses = Wallet.objects.filter(
        network_id=MAINNET_NETWORK_ID,
        deleted=False,
    ).values_list('address', flat=True)
    db_addresses_set = set(db_addresses)
    addresses_not_in_db = all_address_interacted - db_addresses_set
    wallets_to_create = [
        Wallet(
            address=address,
            network_id=MAINNET_NETWORK_ID,
        ) for address in addresses_not_in_db
    ]
    Wallet.objects.bulk_create(wallets_to_create)

    count = 0
    wallet_address_to_model_id = dict()
    all_wallet_models = Wallet.objects.filter(
        network_id=MAINNET_NETWORK_ID,
        address__in=all_address_interacted,
        deleted=False,
    )
    for wallet_model in all_wallet_models:
        # we expect each address to be unique, so don't worry about overriding
        wallet_address_to_model_id[wallet_model.address] = wallet_model.id

    for user_addr in all_address_interacted:

        checksum_user_addr = Web3.to_checksum_address(user_addr)
        results = pool_contract_obj.functions.getUserAccountData(checksum_user_addr).call()

        total_collateral_usd = results[0] / 10 ** 8
        total_debt_eth = results[1] / 10 ** 8
        available_borrow_eth = results[2] / 10 ** 18
        current_liquidation_threshold = results[3] / 10 ** 4
        ltv = results[4] / 10 ** 4
        health_factor = results[5] / 10 ** 18

        #print(user_addr, total_collateral_usd, total_debt_eth, available_borrow_eth, current_liquidation_threshold, ltv, health_factor)

        # not as efficient since it'll update one at a time, but there aren't that many positions right now
        Position.objects.update_or_create(
            contract_id=aave_v3_pool_model.id,
            deleted=False,
            network_id=MAINNET_NETWORK_ID,
            wallet_id=wallet_address_to_model_id[user_addr],
            defaults=dict(
                health_factor=health_factor,
                total_usd_collateral=total_collateral_usd
            ),
        )

        # TODO: add some logging
        count += 1
