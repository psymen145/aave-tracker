// SPDX-License-Identifier: MIT
pragma solidity ^0.8.10;

// ─── Inline interfaces (no npm deps required) ────────────────────────────────

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;
}

interface IPoolAddressesProvider {
    function getPool() external view returns (address);
}

// Uniswap V3 single-hop swap
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24  fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external payable returns (uint256 amountOut);
}

// ─── Contract ─────────────────────────────────────────────────────────────────

/**
 * @title  FlashLiquidator
 * @notice Liquidates undercollateralised Aave V3 positions using a flash loan
 *         so the caller needs only ETH for gas — no debt-token capital required.
 *
 * Flow (single transaction):
 *   1. liquidate()  →  Pool.flashLoanSimple()
 *   2. Pool sends debt tokens to this contract and calls executeOperation()
 *   3. executeOperation():
 *        a. Approves Pool to pull debt tokens
 *        b. Calls Pool.liquidationCall() → receives collateral + bonus
 *        c. Swaps excess collateral → debt token on Uniswap V3
 *        d. Approves Pool to pull back (principal + 0.05 % flash loan fee)
 *        e. Returns true
 *   4. Profit stays in contract; owner calls withdraw() to collect.
 *
 * Profitability condition (approximate):
 *   liquidation_bonus (5–10 %) > flash_loan_fee (0.05 %) + swap_slippage + gas
 *
 * Deployment:
 *   Compile with solc ^0.8.10 or Hardhat/Foundry.
 *   Constructor args: Aave Pool address, Aave PoolAddressesProvider, Uniswap V3 SwapRouter.
 *
 *   Mainnet addresses:
 *     Pool:                   0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2
 *     PoolAddressesProvider:  0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e
 *     Uniswap V3 SwapRouter:  0xE592427A0AEce92De3Edee1F18E0157C05861564
 */
contract FlashLiquidator {
    IPool                  public immutable POOL;
    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    ISwapRouter            public immutable SWAP_ROUTER;
    address                public immutable owner;

    constructor(
        address pool,
        address addressesProvider,
        address swapRouter
    ) {
        POOL               = IPool(pool);
        ADDRESSES_PROVIDER = IPoolAddressesProvider(addressesProvider);
        SWAP_ROUTER        = ISwapRouter(swapRouter);
        owner              = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    // ── Entry point ──────────────────────────────────────────────────────────

    /**
     * @param collateralAsset  The reserve the borrower used as collateral.
     * @param debtAsset        The reserve the borrower owes debt in.
     * @param borrower         Address of the underwater position.
     * @param debtAmount       Amount of debtAsset to flash-borrow and repay
     *                         (set to 50 % of the borrower's debt — Aave's
     *                         close factor — for maximum profit per call).
     * @param swapFee          Uniswap V3 pool fee tier for the collateral→debt
     *                         swap (500 = 0.05 %, 3000 = 0.3 %, 10000 = 1 %).
     */
    function liquidate(
        address collateralAsset,
        address debtAsset,
        address borrower,
        uint256 debtAmount,
        uint24  swapFee
    ) external onlyOwner {
        bytes memory params = abi.encode(collateralAsset, borrower, swapFee);
        POOL.flashLoanSimple(address(this), debtAsset, debtAmount, params, 0);
    }

    // ── Aave flash loan callback ─────────────────────────────────────────────

    /**
     * Called by the Aave Pool immediately after transferring `amount` of
     * `asset` to this contract.  Must approve the Pool to pull back
     * `amount + premium` before returning.
     */
    function executeOperation(
        address asset,      // debt token (flash-loaned)
        uint256 amount,     // principal borrowed
        uint256 premium,    // 0.05 % flash loan fee
        address,            // initiator — unused
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == address(POOL), "Caller not Pool");

        (address collateralAsset, address borrower, uint24 swapFee) =
            abi.decode(params, (address, address, uint24));

        // 1. Approve Pool to pull debt tokens for the liquidation
        IERC20(asset).approve(address(POOL), amount);

        // 2. Liquidate — Aave transfers collateral (+ bonus) to this contract
        POOL.liquidationCall(collateralAsset, asset, borrower, amount, false);

        // 3. Swap all received collateral → debt token to cover repayment
        uint256 collateralBalance = IERC20(collateralAsset).balanceOf(address(this));
        uint256 repayAmount = amount + premium;

        if (collateralAsset != asset && collateralBalance > 0) {
            IERC20(collateralAsset).approve(address(SWAP_ROUTER), collateralBalance);
            SWAP_ROUTER.exactInputSingle(
                ISwapRouter.ExactInputSingleParams({
                    tokenIn:           collateralAsset,
                    tokenOut:          asset,
                    fee:               swapFee,
                    recipient:         address(this),
                    deadline:          block.timestamp,
                    amountIn:          collateralBalance,
                    amountOutMinimum:  repayAmount,  // revert if swap yields too little
                    sqrtPriceLimitX96: 0
                })
            );
        }

        // 4. Approve Pool to pull back principal + fee to close the flash loan
        IERC20(asset).approve(address(POOL), repayAmount);

        return true;
    }

    // ── Profit withdrawal ────────────────────────────────────────────────────

    /**
     * Withdraw accumulated profit (any ERC-20) to the owner.
     * The profit sits in this contract as the leftover after covering the
     * flash loan repayment.
     */
    function withdraw(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "Nothing to withdraw");
        IERC20(token).transfer(owner, balance);
    }
}
