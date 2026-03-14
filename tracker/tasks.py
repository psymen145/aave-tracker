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
    Liquidate an undercollateralised Aave V3 position using a flash loan.

    The on-chain FlashLiquidator contract (contracts/FlashLiquidator.sol)
    handles the full flow in a single transaction:
      1. Flash-borrows the debt token from Aave (0.05 % fee)
      2. Calls liquidationCall() → receives collateral + bonus (5–10 %)
      3. Swaps collateral → debt token on Uniswap V3
      4. Repays the flash loan
      5. Keeps the net profit inside the contract

    This means the caller needs only ETH for gas — no debt-token capital.

    Required env vars:
      LIQUIDATOR_PRIVATE_KEY       – private key (gas money only)
      FLASH_LIQUIDATOR_ADDRESS     – deployed FlashLiquidator contract address
      UNISWAP_SWAP_FEE (optional)  – Uniswap V3 fee tier in bps*100
                                     (500=0.05 %, 3000=0.3 %, 10000=1 %)
                                     defaults to 3000
    """
    private_key = os.environ.get("LIQUIDATOR_PRIVATE_KEY")
    flash_liq_address = os.environ.get("FLASH_LIQUIDATOR_ADDRESS")
    if not private_key:
        logger.error("LIQUIDATOR_PRIVATE_KEY not set — cannot liquidate")
        return {"error": "LIQUIDATOR_PRIVATE_KEY not configured"}
    if not flash_liq_address:
        logger.error("FLASH_LIQUIDATOR_ADDRESS not set — cannot liquidate")
        return {"error": "FLASH_LIQUIDATOR_ADDRESS not configured"}

    swap_fee = int(os.environ.get("UNISWAP_SWAP_FEE", "3000"))

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

    logger.info("Liquidating %s (HF=%.4f) via flash loan", wallet_address, health_factor)

    # Find the reserve with the most collateral and the reserve with the most debt.
    # We pick by raw token balance; for a more accurate ranking you'd convert to
    # USD using the oracle, but largest balance is a good heuristic in practice.
    reserves = pool.functions.getReservesList().call()
    best_collateral_asset = None
    best_collateral_balance = 0
    best_debt_asset = None
    best_debt_balance = 0

    for reserve in reserves:
        reserve_data = pool.functions.getReserveData(reserve).call()
        a_token_addr = reserve_data[8]    # aTokenAddress (collateral)
        var_debt_addr = reserve_data[10]  # variableDebtTokenAddress

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

    # Aave's close factor is 50 %: the most we can liquidate in one call.
    debt_to_cover = best_debt_balance // 2

    logger.info(
        "Flash liquidation for %s: collateral=%s debt=%s debt_to_cover=%s swap_fee=%s",
        wallet_address, best_collateral_asset, best_debt_asset, debt_to_cover, swap_fee,
    )

    # ABI for the single FlashLiquidator.liquidate() entry point
    flash_liq_abi = [
        {
            "inputs": [
                {"internalType": "address", "name": "collateralAsset", "type": "address"},
                {"internalType": "address", "name": "debtAsset",       "type": "address"},
                {"internalType": "address", "name": "borrower",        "type": "address"},
                {"internalType": "uint256", "name": "debtAmount",      "type": "uint256"},
                {"internalType": "uint24",  "name": "swapFee",         "type": "uint24"},
            ],
            "name": "liquidate",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]

    flash_liq = w3.eth.contract(
        address=Web3.to_checksum_address(flash_liq_address),
        abi=flash_liq_abi,
    )

    tx = flash_liq.functions.liquidate(
        best_collateral_asset,
        best_debt_asset,
        user,
        debt_to_cover,
        swap_fee,
    ).build_transaction({
        "from": liquidator_address,
        "nonce": w3.eth.get_transaction_count(liquidator_address),
        "gas": 700_000,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] == 1:
        logger.info("Flash liquidation succeeded for %s, tx=%s", wallet_address, tx_hash.hex())
        return {"success": True, "tx": tx_hash.hex()}
    else:
        logger.error("Flash liquidation reverted for %s, tx=%s", wallet_address, tx_hash.hex())
        return {"error": "Transaction reverted", "tx": tx_hash.hex()}


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
