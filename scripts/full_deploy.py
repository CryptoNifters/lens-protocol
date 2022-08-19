"""
Script to deploy the full LensHub system.
"""

# Imports
import os
import json
from distutils.util import strtobool

from brownie import accounts, web3, network, project, Contract

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Define constants
TREASURY_FEE_BPS = 50
LENS_HUB_NFT_NAME = "Lens Protocol Profiles"
LENS_HUB_NFT_SYMBOL = "LPP"
# Use Ganache-GUI as the default network provider.
# NETWORK_ID should be the network ID of the network you want to connect to.
NETWORK_ID = os.getenv("NETWORK_ID", "live")
# Use 'local' as the default network type. Possible options are 'local', 'testnet', and 'mainnet'
NETWORK_TYPE = os.getenv("NETWORK_TYPE", "local")
CONTRACT_VERIFICATION = strtobool(os.getenv("CONTRACT_VERIFICATION", "True"))
# Private Keys
DEPLOYER_PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")
GOVERNANCE_PRIVATE_KEY = os.getenv("GOVERNANCE_PRIVATE_KEY")
TREASURY_PRIVATE_KEY = os.getenv("TREASURY_PRIVATE_KEY")

if NETWORK_TYPE == "local":
    # Setting CONTRACT_VERIFICATION to False for local because Ganache-GUI does not support the
    # verification of contracts.
    CONTRACT_VERIFICATION = False


def main():
    """
    Main function.
    Executes the full deployment of the LensHub system.
    """
    # Connect to the network
    network.connect(NETWORK_ID)
    if network.is_connected():
        print("Connected to network: " + NETWORK_ID)
    else:
        raise ConnectionError("Could not connect to network: " + NETWORK_ID)

    # Add accounts to the project from the environment variables
    for account_private_key in [
        DEPLOYER_PRIVATE_KEY,
        GOVERNANCE_PRIVATE_KEY,
        TREASURY_PRIVATE_KEY,
    ]:
        if account_private_key is not None:
            accounts.add(account_private_key)

    # Load the project and define the accounts
    lenshubProject = project.load(".")

    deployer = accounts[0]
    governance = accounts[1]
    treasuryAddress = accounts[2].address
    proxyAdminAddress = profileCreatorAddress = deployer.address

    # Get the nonce of deployer
    deployerNonce = web3.eth.getTransactionCount(deployer.address)

    # Deploy the LensHub system
    # Deploy the Module Globals
    print("-- Deploying Module Globals")
    moduleGlobals = lenshubProject.ModuleGlobals.deploy(
        governance.address,
        treasuryAddress,
        TREASURY_FEE_BPS,
        {"from": deployer, "nonce": deployerNonce},
        publish_source=CONTRACT_VERIFICATION,
    )
    deployerNonce += 1

    # Deploying Logic Libs
    print("-- Deploying Logic Libs")
    publishingLogic = lenshubProject.PublishingLogic.deploy(
        {"from": deployer, "nonce": deployerNonce}, publish_source=CONTRACT_VERIFICATION
    )
    deployerNonce += 1

    interactionLogic = lenshubProject.InteractionLogic.deploy(
        {"from": deployer, "nonce": deployerNonce}, publish_source=CONTRACT_VERIFICATION
    )
    deployerNonce += 1

    profileTokenURILogic = lenshubProject.ProfileTokenURILogic.deploy(
        {"from": deployer, "nonce": deployerNonce}, publish_source=CONTRACT_VERIFICATION
    )
    deployerNonce += 1

    # Updating the LensHub Bytecode to replace the Logic Libs addresses
    lhcjson = json.load(open("build/contracts/LensHub.json", "r", encoding="utf-8"))
    _bytecode_lh = lhcjson["deployedBytecode"]
    # Update PublishingLogic address
    _bytecode_lh = _bytecode_lh.replace(
        "__$1f7cbacb1f9f5d323b85b0487838426c8d$__",
        publishingLogic.address.replace("0x", "").lower(),
    )

    # Update InteractionLogic address
    _bytecode_lh = _bytecode_lh.replace(
        "__$1e68a60ae0444699fe08192a29ecc09930$__",
        interactionLogic.address.replace("0x", "").lower(),
    )

    # Update ProfileTokenURILogic address
    _bytecode_lh = _bytecode_lh.replace(
        "__$f906f20d797116ee89ed79945048c6ad36$__",
        profileTokenURILogic.address.replace("0x", "").lower(),
    )
    lhcjson["deployedBytecode"] = _bytecode_lh
    json.dump(lhcjson, open("build/contracts/LensHub.json", "w", encoding="utf-8"))

    # Here, we pre-compute the nonces and addresses used to deploy the contracts.
    followNFTNonce = deployerNonce + 1
    collectNFTNonce = deployerNonce + 2
    hubProxyNonce = deployerNonce + 3

    followNFTImplAddress = deployer.get_deployment_address(followNFTNonce)
    collectNFTImplAddress = deployer.get_deployment_address(collectNFTNonce)
    hubProxyAddress = deployer.get_deployment_address(hubProxyNonce)

    # We deploy first the hub implementation, then the followNFT implementation, the collectNFT,
    # and finally the hub proxy with initialization.
    print("-- Deploying Hub Implementation")
    lensHubImpl = lenshubProject.LensHub.deploy(
        followNFTImplAddress,
        collectNFTImplAddress,
        {"from": deployer, "nonce": deployerNonce},
        publish_source=CONTRACT_VERIFICATION,
    )
    deployerNonce += 1

    print("-- Deploying Follow & Collect NFT Implementations")
    lenshubProject.FollowNFT.deploy(
        hubProxyAddress,
        {"from": deployer, "nonce": deployerNonce},
        publish_source=CONTRACT_VERIFICATION,
    )
    deployerNonce += 1

    lenshubProject.CollectNFT.deploy(
        hubProxyAddress,
        {"from": deployer, "nonce": deployerNonce},
        publish_source=CONTRACT_VERIFICATION,
    )
    deployerNonce += 1

    print("-- Deploying Hub Proxy")
    data_lh_init = lenshubProject.interface.ILensHub(
        lensHubImpl.address
    ).initialize.encode_input(
        LENS_HUB_NFT_NAME, LENS_HUB_NFT_SYMBOL, governance.address
    )
    proxy = lenshubProject.TransparentUpgradeableProxy.deploy(
        lensHubImpl.address,
        proxyAdminAddress,
        data_lh_init,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Connect the hub proxy to the LensHub factory and the governance for ease of use.
    lensHub = Contract.from_abi("LensHub", proxy.address, lensHubImpl.abi, governance)

    print("-- Deploying Lens Periphery")
    lensPeriphery = lenshubProject.LensPeriphery.deploy(
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Currency
    print("-- Deploying Currency")
    currency = lenshubProject.Currency.deploy(
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Deploy Collect modules
    print("-- Deploying feeCollectModule")
    feeCollectModule = lenshubProject.FeeCollectModule.deploy(
        lensHub.address,
        moduleGlobals.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying limitedFeeCollectModule")
    limitedFeeCollectModule = lenshubProject.LimitedFeeCollectModule.deploy(
        lensHub.address,
        moduleGlobals.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying timedFeeCollectModule")
    timedFeeCollectModule = lenshubProject.TimedFeeCollectModule.deploy(
        lensHub.address,
        moduleGlobals.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying limitedTimedFeeCollectModule")
    limitedTimedFeeCollectModule = lenshubProject.LimitedTimedFeeCollectModule.deploy(
        lensHub.address,
        moduleGlobals.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying revertCollectModule")
    revertCollectModule = lenshubProject.RevertCollectModule.deploy(
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying freeCollectModule")
    freeCollectModule = lenshubProject.FreeCollectModule.deploy(
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Deploy Follow modules
    print("-- Deploying feeFollowModule")
    feeFollowModule = lenshubProject.FeeFollowModule.deploy(
        lensHub.address,
        moduleGlobals.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying profileFollowModule")
    profileFollowModule = lenshubProject.ProfileFollowModule.deploy(
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying revertFollowModule")
    revertFollowModule = lenshubProject.RevertFollowModule.deploy(
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Deploy reference module
    print("-- Deploying followerOnlyReferenceModule")
    followerOnlyReferenceModule = lenshubProject.FollowerOnlyReferenceModule.deploy(
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Deploy UIDataProvider
    print("-- Deploying followerOnlyReferenceModule")
    uiDataProvider = lenshubProject.UIDataProvider.deploy(
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    print("-- Deploying profileCreationProxy")
    profileCreationProxy = lenshubProject.ProfileCreationProxy.deploy(
        profileCreatorAddress,
        lensHub.address,
        {"from": deployer, "nonce": deployerNonce},
    )
    deployerNonce += 1

    # Whitelist collect modules
    print("-- Whitelisting collect modules")
    governanceNonce = web3.eth.getTransactionCount(governance.address)

    lensHub.whitelistCollectModule(
        feeCollectModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lensHub.whitelistCollectModule(
        limitedFeeCollectModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lensHub.whitelistCollectModule(
        timedFeeCollectModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lensHub.whitelistCollectModule(
        limitedTimedFeeCollectModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lensHub.whitelistCollectModule(
        revertCollectModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lensHub.whitelistCollectModule(
        freeCollectModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    print("-- Whitelisting follow modules")
    lensHub.whitelistFollowModule(
        feeFollowModule.address, True, {"from": governance, "nonce": governanceNonce}
    )
    governanceNonce += 1

    lensHub.whitelistFollowModule(
        profileFollowModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lensHub.whitelistFollowModule(
        revertFollowModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    print("-- Whitelisting reference modules")
    lensHub.whitelistReferenceModule(
        followerOnlyReferenceModule.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    # Whitelist Currency
    print("-- Whitelisting Currency in Module Globals")
    moduleGlobals.whitelistCurrency(
        currency.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    # Whitelist the profile creation proxy
    print("-- Whitelisting Profile Creation Proxy")
    lensHub.whitelistProfileCreator(
        profileCreationProxy.address,
        True,
        {"from": governance, "nonce": governanceNonce},
    )
    governanceNonce += 1

    lenshubAddresses = {
        "lensHub proxy": lensHub.address,
        "lensHub impl:": lensHubImpl.address,
        "publishing logic lib": publishingLogic.address,
        "interaction logic lib": interactionLogic.address,
        "follow NFT impl": followNFTImplAddress,
        "collect NFT impl": collectNFTImplAddress,
        "currency": currency.address,
        "lens periphery": lensPeriphery.address,
        "module globals": moduleGlobals.address,
        "fee collect module": feeCollectModule.address,
        "limited fee collect module": limitedFeeCollectModule.address,
        "timed fee collect module": timedFeeCollectModule.address,
        "limited timed fee collect module": limitedTimedFeeCollectModule.address,
        "revert collect module": revertCollectModule.address,
        "free collect module": freeCollectModule.address,
        "fee follow module": feeFollowModule.address,
        "profile follow module": profileFollowModule.address,
        "revert follow module": revertFollowModule.address,
        "follower only reference module": followerOnlyReferenceModule.address,
        "UI data provider": uiDataProvider.address,
        "Profile creation proxy": profileCreationProxy.address,
    }
    json.dump(
        lenshubAddresses,
        open(f"build/lenshubAddresses-{NETWORK_ID}.json", "w", encoding="utf-8"),
    )


if __name__ == "__main__":
    main()
    print("Deployment completed")
