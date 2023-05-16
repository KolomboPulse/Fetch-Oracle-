import asyncio
import io
from contextlib import ExitStack
from typing import Optional
from unittest.mock import mock_open
from unittest.mock import patch

import async_timeout
import pytest
from chained_accounts import ChainedAccount
from telliot_core.apps.core import TelliotConfig
from telliot_core.apps.core import TelliotCore
from telliot_feeds.feeds import evm_call_feed_example
from telliot_feeds.queries.price.spot_price import SpotPrice
from web3 import Web3

from tellor_disputables.cli import start


wallet = "0x39E419bA25196794B595B2a595Ea8E527ddC9856"


def txn_kwargs(w3: Web3) -> dict:
    return {
        "gas": 500000,
        "gasPrice": w3.eth.gas_price,
        "nonce": w3.eth.get_transaction_count(wallet),
        "from": wallet,
    }


eth = SpotPrice("eth", "usd")
btc = SpotPrice("btc", "usd")
eth_query_id, eth_query_data = Web3.toHex(eth.query_id), Web3.toHex(eth.query_data)
btc_query_id, btc_query_data = Web3.toHex(btc.query_id), Web3.toHex(btc.query_data)
evm_query_id, evm_query_data = Web3.toHex(evm_call_feed_example.query.query_id), Web3.toHex(
    evm_call_feed_example.query.query_data
)
evm_wrong_val = evm_call_feed_example.query.value_type.encode((int.to_bytes(12345, 32, "big"), 1683131435)).hex()


def custom_open_side_effect(*args, **kwargs):
    """mocks open function to return a mock file"""
    if args[0] == "disputer-config.yaml":
        return mock_open().return_value
    return io.open(*args, **kwargs)


def increase_time_and_mine_blocks(w3: Web3, seconds: int, num_blocks: Optional[int] = None):
    # Increase time
    w3.provider.make_request("evm_increaseTime", [seconds])

    # Mine new blocks
    if num_blocks is None:
        w3.provider.make_request("evm_mine", [])
    else:
        for _ in range(num_blocks):
            w3.provider.make_request("evm_mine", [])


@pytest.fixture(scope="function")
async def environment_setup(setup: TelliotConfig, disputer_account: ChainedAccount):
    config = setup
    # only configure two required endpoints cause tests take too long
    config.endpoints.endpoints = [config.endpoints.find(chain_id=1)[0], config.endpoints.find(chain_id=1337)[0]]
    node = config.get_endpoint()
    node.connect()

    w3 = node._web3
    increase_time_and_mine_blocks(w3, 600, 20)
    async with TelliotCore(config=config) as core:
        account = disputer_account
        contracts = core.get_tellor360_contracts()
        oracle = contracts.oracle
        token = contracts.token
        approve = token.contract.get_function_by_name("approve")
        transfer = token.contract.get_function_by_name("transfer")
        deposit_stake = oracle.contract.get_function_by_name("depositStake")
        submit_value = oracle.contract.get_function_by_name("submitValue")

        # transfer trb to disputer account for disputing
        token_txn = transfer(w3.toChecksumAddress(account.address), int(100e18)).buildTransaction(txn_kwargs(w3))
        token_hash = w3.eth.send_transaction(token_txn)
        reciept = w3.eth.wait_for_transaction_receipt(token_hash)
        assert reciept["status"] == 1
        # approve oracle to spend trb for submitting values
        approve_txn = approve(oracle.address, int(10000e18)).buildTransaction(txn_kwargs(w3))
        approve_hash = w3.eth.send_transaction(approve_txn)
        reciept = w3.eth.wait_for_transaction_receipt(approve_hash)
        assert reciept["status"] == 1
        # deposit stake
        deposit_txn = deposit_stake(_amount=int(10000e18)).buildTransaction(txn_kwargs(w3))
        deposit_hash = w3.eth.send_transaction(deposit_txn)
        receipt = w3.eth.wait_for_transaction_receipt(deposit_hash)
        assert receipt["status"] == 1
        # submit bad eth value
        submit_value_txn = submit_value(eth_query_id, int.to_bytes(14, 32, "big"), 0, eth_query_data).buildTransaction(
            txn_kwargs(w3)
        )
        submit_value_hash = w3.eth.send_transaction(submit_value_txn)
        receipt = w3.eth.wait_for_transaction_receipt(submit_value_hash)
        assert receipt["status"] == 1
        # submit bad btc value
        # bypass reporter lock
        increase_time_and_mine_blocks(w3, 4300)
        submit_value_txn = submit_value(btc_query_id, int.to_bytes(13, 32, "big"), 0, btc_query_data).buildTransaction(
            txn_kwargs(w3)
        )
        submit_value_hash = w3.eth.send_transaction(submit_value_txn)
        reciept = w3.eth.wait_for_transaction_receipt(submit_value_hash)
        assert reciept["status"] == 1
        # submit bad evmcall value
        # bypass reporter lock
        increase_time_and_mine_blocks(w3, 4300)
        submit_value_txn = submit_value(evm_query_id, evm_wrong_val, 0, evm_query_data).buildTransaction(txn_kwargs(w3))
        submit_value_hash = w3.eth.send_transaction(submit_value_txn)
        reciept = w3.eth.wait_for_transaction_receipt(submit_value_hash)
        assert reciept["status"] == 1
        return config, oracle, w3


@pytest.mark.asyncio
async def fetch_timestamp(oracle, query_id, chain_timestamp):
    """fetches a value's timestamp from oracle"""
    timestamp, status = await oracle.read("getDataBefore", query_id, chain_timestamp)
    assert timestamp[2] > 0
    assert status.ok, status.error
    return timestamp


async def check_dispute(oracle, query_id, timestamp):
    """checks if a value is in dispute"""
    indispute, _ = await oracle.read("isInDispute", query_id, timestamp[2])
    return indispute


async def setup_and_start(config, config_patches=None):
    # using exit stack makes nested patching easier to read
    with ExitStack() as stack:
        stack.enter_context(patch("getpass.getpass", return_value=""))
        stack.enter_context(patch("tellor_disputables.alerts.send_text_msg", side_effect=print("alert sent")))
        stack.enter_context(patch("tellor_disputables.cli.TelliotConfig", new=lambda: config))
        stack.enter_context(patch("telliot_feeds.feeds.evm_call_feed.source.cfg", config))

        if config_patches is not None:
            for p in config_patches:
                stack.enter_context(p)

        try:
            async with async_timeout.timeout(9):
                await start(False, 8, "disputer-test-acct", True, 0.1)
        except asyncio.TimeoutError:
            pass


@pytest.mark.asyncio
async def test_default_config(environment_setup):
    """Test that the default config works as expected"""
    config, oracle, w3 = await environment_setup
    chain_timestamp = w3.eth.get_block("latest")["timestamp"]

    eth_timestamp = await fetch_timestamp(oracle, eth_query_id, chain_timestamp)
    evm_timestamp = await fetch_timestamp(oracle, evm_query_id, chain_timestamp + 5000)
    btc_timestamp = await fetch_timestamp(oracle, btc_query_id, chain_timestamp + 10000)

    await setup_and_start(config)
    # not in config file
    assert not await check_dispute(oracle, btc_query_id, btc_timestamp)
    # in config file
    assert await check_dispute(oracle, eth_query_id, eth_timestamp)
    assert await check_dispute(oracle, evm_query_id, evm_timestamp)


@pytest.mark.asyncio
async def test_custom_btc_config(environment_setup):
    """Test that a custom btc config works as expected"""
    config, oracle, w3 = await environment_setup
    chain_timestamp = w3.eth.get_block("latest")["timestamp"]

    eth_timestamp = await fetch_timestamp(oracle, eth_query_id, chain_timestamp)
    evm_timestamp = await fetch_timestamp(oracle, evm_query_id, chain_timestamp + 5000)
    btc_timestamp = await fetch_timestamp(oracle, btc_query_id, chain_timestamp + 10000)

    btc_config = {"feeds": [{"query_id": btc_query_id, "threshold": {"type": "Percentage", "amount": 0.75}}]}
    config_patches = [
        patch("builtins.open", side_effect=custom_open_side_effect),
        patch("yaml.safe_load", return_value=btc_config),
    ]
    await setup_and_start(config, config_patches)

    assert await check_dispute(oracle, btc_query_id, btc_timestamp)
    # not in config file
    assert not await check_dispute(oracle, eth_query_id, eth_timestamp)
    assert not await check_dispute(oracle, evm_query_id, evm_timestamp)


@pytest.mark.asyncio
async def test_custom_eth_btc_config(environment_setup):
    """Test that eth and btc in dispute config"""
    config, oracle, w3 = await environment_setup
    chain_timestamp = w3.eth.get_block("latest")["timestamp"]

    eth_timestamp = await fetch_timestamp(oracle, eth_query_id, chain_timestamp)
    evm_timestamp = await fetch_timestamp(oracle, evm_query_id, chain_timestamp + 5000)
    btc_timestamp = await fetch_timestamp(oracle, btc_query_id, chain_timestamp + 10000)

    btc_eth_config = {
        "feeds": [
            {"query_id": btc_query_id, "threshold": {"type": "Percentage", "amount": 0.75}},
            {"query_id": eth_query_id, "threshold": {"type": "Percentage", "amount": 0.75}},
        ]
    }
    config_patches = [
        patch("builtins.open", side_effect=custom_open_side_effect),
        patch("yaml.safe_load", return_value=btc_eth_config),
    ]
    await setup_and_start(config, config_patches)

    assert await check_dispute(oracle, btc_query_id, btc_timestamp)
    assert await check_dispute(oracle, eth_query_id, eth_timestamp)
    # not in config file
    assert not await check_dispute(oracle, evm_query_id, evm_timestamp)


@pytest.mark.asyncio
async def test_get_source_from_data(environment_setup, caplog):
    """Test when get_source_from_data function returns None"""
    config, _, _ = await environment_setup

    config_patches = [
        patch("tellor_disputables.data.get_source_from_data", side_effect=lambda _: None),
    ]
    await setup_and_start(config, config_patches)
    assert "Unable to form source from queryData of query type EVMCall" in caplog.text