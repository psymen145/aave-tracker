from celery import shared_task
from web3 import Web3
import os
import logging

from tracker.models import Contract, Wallet, Position

logger = logging.getLogger(__name__)

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
    if not logs:
        logger.error("No logs returned from Etherscan — aborting pull_positions")
        return

    all_address_interacted = set()
    for log in logs:
        topics = log.get("topics")
        if not topics:
            logger.error("error, missing topics")
            continue

        # topics are a list of params used, first is always the event signature
        # addresses are padded
        user_addr = f"0x{topics[2][26:]}"

        all_address_interacted.add(user_addr)

    logger.info(f"{len(all_address_interacted)} addresses supplied to with aave v3 eth mainnet")

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

    logger.info(
        "Finished running pull_positions",
        extra={
            "updated_or_created_position_count": count
        }
    )

PAGE_SIZE = 1000

# Minimal ERC-20 ABI — only the functions needed for liquidation
ERC20_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


@shared_task
def liquidate_position(wallet_address: str) -> dict:
    """
    Liquidate an undercollateralised Aave V3 position.

    How it works:
      1. Verify the health factor is below 1.0.
      2. Iterate every Aave reserve to find the user's highest-value collateral
         asset (aToken balance) and highest-value debt asset (variableDebtToken
         balance).
      3. Approve the Pool to spend our debt token.
      4. Call liquidationCall() — Aave automatically caps the repayment at 50 %
         of the debt (the protocol's close factor), so passing uint256.max is
         safe and repays the maximum allowed in one transaction.
      5. Return the tx hash on success.

    The liquidation bonus (5–10 % depending on the collateral asset) is the
    profit: you repay X of debt and receive X * (1 + bonus) of collateral.

    Required env var:
      LIQUIDATOR_PRIVATE_KEY  – private key of a wallet that holds the debt
                                 token and enough ETH for gas.
    """
    private_key = os.environ.get("LIQUIDATOR_PRIVATE_KEY")
    if not private_key:
        logger.error("LIQUIDATOR_PRIVATE_KEY not set — cannot liquidate")
        return {"error": "LIQUIDATOR_PRIVATE_KEY not configured"}

    w3 = Web3(Web3.HTTPProvider(endpoint))
    liquidator_address = w3.eth.account.from_key(private_key).address

    pool_model = Contract.objects.get(name="Pool", network_id=MAINNET_NETWORK_ID)
    pool = w3.eth.contract(address=pool_model.address, abi=pool_model.abi)

    user = Web3.to_checksum_address(wallet_address)

    # Confirm the position is still liquidatable (HF may have recovered)
    account_data = pool.functions.getUserAccountData(user).call()
    health_factor = account_data[5] / 10 ** 18
    if health_factor >= 1.0:
        msg = f"Position not liquidatable (HF={health_factor:.4f})"
        logger.info("%s — %s", wallet_address, msg)
        return {"error": msg}

    logger.info("Liquidating %s (HF=%.4f)", wallet_address, health_factor)

    # Find the reserve with the most collateral and the reserve with the most debt
    reserves = pool.functions.getReservesList().call()
    best_collateral_asset = None
    best_collateral_balance = 0
    best_debt_asset = None
    best_debt_balance = 0

    for reserve in reserves:
        reserve_data = pool.functions.getReserveData(reserve).call()
        a_token_addr = reserve_data[8]        # aTokenAddress (collateral)
        var_debt_addr = reserve_data[10]      # variableDebtTokenAddress

        a_token = w3.eth.contract(address=a_token_addr, abi=ERC20_ABI)
        var_debt_token = w3.eth.contract(address=var_debt_addr, abi=ERC20_ABI)

        collateral_bal = a_token.functions.balanceOf(user).call()
        debt_bal = var_debt_token.functions.balanceOf(user).call()

        if collateral_bal > best_collateral_balance:
            best_collateral_balance = collateral_bal
            best_collateral_asset = reserve

        if debt_bal > best_debt_balance:
            best_debt_balance = debt_bal
            best_debt_asset = reserve

    if not best_collateral_asset or not best_debt_asset:
        msg = "Could not find collateral or debt asset for user"
        logger.error("%s — %s", wallet_address, msg)
        return {"error": msg}

    logger.info(
        "Best pair for %s: collateral=%s debt=%s debt_balance=%s",
        wallet_address, best_collateral_asset, best_debt_asset, best_debt_balance,
    )

    MAX_UINT256 = 2 ** 256 - 1

    # Approve the Pool to pull our debt tokens
    debt_token = w3.eth.contract(address=best_debt_asset, abi=ERC20_ABI)
    approve_tx = debt_token.functions.approve(
        pool_model.address, MAX_UINT256
    ).build_transaction({
        "from": liquidator_address,
        "nonce": w3.eth.get_transaction_count(liquidator_address),
        "gas": 100_000,
    })
    signed_approve = w3.eth.account.sign_transaction(approve_tx, private_key)
    approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
    w3.eth.wait_for_transaction_receipt(approve_hash)
    logger.info("Approved debt token spend, approve_tx=%s", approve_hash.hex())

    # Execute the liquidation
    liq_tx = pool.functions.liquidationCall(
        best_collateral_asset,   # collateralAsset
        best_debt_asset,         # debtAsset
        user,                    # borrower to liquidate
        MAX_UINT256,             # debtToCover — Aave caps at 50 % automatically
        False,                   # receiveAToken — receive underlying collateral
    ).build_transaction({
        "from": liquidator_address,
        "nonce": w3.eth.get_transaction_count(liquidator_address),
        "gas": 500_000,
    })
    signed_liq = w3.eth.account.sign_transaction(liq_tx, private_key)
    liq_hash = w3.eth.send_raw_transaction(signed_liq.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(liq_hash)

    if receipt["status"] == 1:
        logger.info("Liquidation succeeded for %s, tx=%s", wallet_address, liq_hash.hex())
        return {"success": True, "tx": liq_hash.hex()}
    else:
        logger.error("Liquidation reverted for %s, tx=%s", wallet_address, liq_hash.hex())
        return {"error": "Transaction reverted", "tx": liq_hash.hex()}


def get_logs(from_block, to_block, address, topic0, api_key):
    import requests

    url = "https://api.etherscan.io/api"
    all_results = []
    page = 1

    while True:
        querystring = {
            "module": "logs",
            "action": "getLogs",
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": address,
            "topic0": topic0,
            "page": page,
            "offset": PAGE_SIZE,
            "apikey": api_key,
        }

        response = requests.get(url, params=querystring)
        data = response.json()

        if data.get("status") != "1":
            logger.error(
                "Etherscan getLogs error (page %d): %s",
                page,
                data.get("message") or data.get("result"),
            )
            break

        results = data["result"]
        all_results.extend(results)

        if len(results) < PAGE_SIZE:
            break  # last page reached

        page += 1

    return all_results


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
