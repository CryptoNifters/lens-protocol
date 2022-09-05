"""
Implementation of helper functions for custom verification due to bugs in the 
Brownie `publish_source` feature.
"""

from typing import Dict
import io
import os
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from brownie import network
from brownie._config import CONFIG, REQUEST_HEADERS
from brownie.network.contract import _explorer_tokens
from brownie.network.web3 import _resolve_address
from brownie.utils import color
from brownie.project import compiler
from brownie.project.flattener import Flattener

def get_verification_info(self: network.contract.ContractContainer) -> Dict:
    """
    Return a dict with flattened source code for this contract
    and further information needed for verification
    """
    language = self._build["language"]
    if language == "Vyper":
        raise TypeError(
            "Etherscan does not support API verification of source code "
            "for vyper contracts. You need to verify the source manually"
        )
    elif language == "Solidity":
        if self._flattener is None:
            source_fp = (
                Path(self._project._path)
                .joinpath(self._build["sourcePath"])
                .resolve()
                .as_posix()
            )
            config = self._project._compiler_config
            remaps = dict(
                map(
                    lambda s: s.split("=", 1),
                    compiler._get_solc_remappings(config["solc"]["remappings"]),
                )
            )
            libs = {lib.strip("_") for lib in re.findall("_{1,}[^_]*_{1,}", self.bytecode)}
            libraries = {}
            for lib in libs:
                lib_source_fp = Path(self._sources.get_source_path(lib)).name
                if lib_source_fp in libraries:
                    libraries[lib_source_fp][lib] = self._project[lib][-1].address
                else:
                    libraries[lib_source_fp] = {lib: self._project[lib][-1].address}
            compiler_settings = {
                "evmVersion": self._build["compiler"]["evm_version"],
                "optimizer": config["solc"]["optimizer"],
                "libraries": libraries,
            }
            self._flattener = Flattener(source_fp, self._name, remaps, compiler_settings)

        build_json = self._build

        return {
            "standard_json_input": self._flattener.standard_input_json,
            "contract_name": build_json["contractName"],
            "compiler_version": build_json["compiler"]["version"],
            "optimizer_enabled": build_json["compiler"]["optimizer"]["enabled"],
            "optimizer_runs": build_json["compiler"]["optimizer"]["runs"],
            "license_identifier": self._flattener.license,
            "bytecode_len": len(build_json["bytecode"]),
        }
    else:
        raise TypeError(f"Unsupported language for source verification: {language}")

def publish_source(self: network.contract.ContractContainer, 
                   contract: network.contract.ProjectContract, 
                   silent: bool = False) -> bool:
    """Flatten contract and publish source on the selected explorer"""

    # Check required conditions for verifying
    url = CONFIG.active_network.get("explorer")
    if url is None:
        raise ValueError("Explorer API not set for this network")
    env_token = next((v for k, v in _explorer_tokens.items() if k in url), None)
    if env_token is None:
        raise ValueError(
            f"Publishing source is only supported on {', '.join(_explorer_tokens)},"
            "change the Explorer API"
        )

    if os.getenv(env_token):
        api_key = os.getenv(env_token)
    else:
        host = urlparse(url).netloc
        host = host[host.index(".") + 1 :]
        raise ValueError(
            f"An API token is required to verify contract source code. Visit https://{host}/ "
            f"to obtain a token, and then store it as the environment variable ${env_token}"
        )

    address = _resolve_address(contract.address)

    # Get source code and contract/compiler information
    contract_info = get_verification_info(self)

    # Select matching license code (https://etherscan.io/contract-license-types)
    license_code = 1
    identifier = contract_info["license_identifier"].lower()
    if "unlicensed" in identifier:
        license_code = 2
    elif "mit" in identifier:
        license_code = 3
    elif "agpl" in identifier and "3.0" in identifier:
        license_code = 13
    elif "lgpl" in identifier:
        if "2.1" in identifier:
            license_code = 6
        elif "3.0" in identifier:
            license_code = 7
    elif "gpl" in identifier:
        if "2.0" in identifier:
            license_code = 4
        elif "3.0" in identifier:
            license_code = 5
    elif "bsd-2-clause" in identifier:
        license_code = 8
    elif "bsd-3-clause" in identifier:
        license_code = 9
    elif "mpl" in identifier and "2.0" in identifier:
        license_code = 10
    elif identifier.startswith("osl") and "3.0" in identifier:
        license_code = 11
    elif "apache" in identifier and "2.0" in identifier:
        license_code = 12

    # get constructor arguments
    params_tx: Dict = {
        "apikey": api_key,
        "module": "account",
        "action": "txlist",
        "address": address,
        "page": 1,
        "sort": "asc",
        "offset": 1,
    }
    i = 0
    while True:
        response = requests.get(url, params=params_tx, headers=REQUEST_HEADERS)
        if response.status_code != 200:
            raise ConnectionError(
                f"Status {response.status_code} when querying {url}: {response.text}"
            )
        data = response.json()
        if int(data["status"]) == 1:
            # Constructor arguments received
            break
        else:
            # Wait for contract to be recognized by etherscan
            # This takes a few seconds after the contract is deployed
            # After 10 loops we throw with the API result message (includes address)
            if i >= 10:
                raise ValueError(f"API request failed with: {data['result']}")
            elif i == 0 and not silent:
                print(f"Waiting for {url} to process contract...")
            i += 1
            time.sleep(10)

    if data["message"] == "OK":
        constructor_arguments = data["result"][0]["input"][contract_info["bytecode_len"] + 2 :]
    else:
        constructor_arguments = ""

    # Submit verification
    payload_verification: Dict = {
        "apikey": api_key,
        "module": "contract",
        "action": "verifysourcecode",
        "contractaddress": address,
        "sourceCode": io.StringIO(json.dumps(self._flattener.standard_input_json)),
        "codeformat": "solidity-standard-json-input",
        "contractname": f"{self._flattener.contract_file}:{self._flattener.contract_name}",
        "compilerversion": f"v{contract_info['compiler_version']}",
        "optimizationUsed": 1 if contract_info["optimizer_enabled"] else 0,
        "runs": contract_info["optimizer_runs"],
        "constructorArguements": constructor_arguments,
        "licenseType": license_code,
    }
    response = requests.post(url, data=payload_verification, headers=REQUEST_HEADERS)
    if response.status_code != 200:
        raise ConnectionError(
            f"Status {response.status_code} when querying {url}: {response.text}"
        )
    data = response.json()
    if int(data["status"]) != 1:
        raise ValueError(f"Failed to submit verification request: {data['result']}")

    # Status of request
    guid = data["result"]
    if not silent:
        print("Verification submitted successfully. Waiting for result...")
    time.sleep(10)
    params_status: Dict = {
        "apikey": api_key,
        "module": "contract",
        "action": "checkverifystatus",
        "guid": guid,
    }
    while True:
        response = requests.get(url, params=params_status, headers=REQUEST_HEADERS)
        if response.status_code != 200:
            raise ConnectionError(
                f"Status {response.status_code} when querying {url}: {response.text}"
            )
        data = response.json()
        if data["result"] == "Pending in queue":
            if not silent:
                print("Verification pending...")
        else:
            if not silent:
                col = "bright green" if data["message"] == "OK" else "bright red"
                print(f"Verification complete. Result: {color(col)}{data['result']}{color}")
            return data["message"] == "OK"
        time.sleep(10)
