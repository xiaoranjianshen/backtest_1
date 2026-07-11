# -*- coding: utf-8 -*-
import unittest

from config import FEE_DICT, SYMBOL_DICT


class ContractMetadataTest(unittest.TestCase):
    def test_symbol_and_fee_metadata_have_matching_contract_specs(self):
        fee_by_lower = {key.lower(): value for key, value in FEE_DICT.items()}
        mismatches = []
        for code, (multiplier, tick_size, _exchange, _sector) in SYMBOL_DICT.items():
            fee_meta = fee_by_lower[code.lower()]
            if float(multiplier) != float(fee_meta["multiplier"]):
                mismatches.append((code, "multiplier", multiplier, fee_meta["multiplier"]))
            if float(tick_size) != float(fee_meta["tick_size"]):
                mismatches.append((code, "tick_size", tick_size, fee_meta["tick_size"]))

        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
