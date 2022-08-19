// SPDX-License-Identifier: MIT

pragma solidity 0.8.10;

import {ILensHub} from '../interfaces/ILensHub.sol';
import {Proxy} from 'OpenZeppelin/openzeppelin-contracts@4.5.0/contracts/proxy/Proxy.sol';
import {Address} from 'OpenZeppelin/openzeppelin-contracts@4.5.0/contracts/utils/Address.sol';

contract FollowNFTProxy is Proxy {
    using Address for address;
    address immutable HUB;

    constructor(bytes memory data) {
        HUB = msg.sender;
        ILensHub(msg.sender).getFollowNFTImpl().functionDelegateCall(data);
    }

    function _implementation() internal view override returns (address) {
        return ILensHub(HUB).getFollowNFTImpl();
    }
}
