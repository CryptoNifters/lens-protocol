// SPDX-License-Identifier: MIT

pragma solidity 0.8.10;

import {ERC20} from 'OpenZeppelin/openzeppelin-contracts@4.5.0/contracts/token/ERC20/ERC20.sol';

contract Currency is ERC20('Currency', 'CRNC') {
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
