import json
import tempfile
import unittest

import auth


VALID_WALLET = "GL5jR8Hs8RDqhBGP2GJ6kVDCjgHMBRt5ffLucHiDcKbM"


class GatingConfigTests(unittest.TestCase):
    def setUp(self):
        self._orig_config = auth.CONFIG
        self._tmp = tempfile.TemporaryDirectory()
        auth.CONFIG = f"{self._tmp.name}/config.json"

    def tearDown(self):
        auth.CONFIG = self._orig_config
        self._tmp.cleanup()

    def write_gating(self, gating):
        with open(auth.CONFIG, "w", encoding="utf-8") as f:
            json.dump({"gating": gating}, f)

    def test_numeric_strings_and_boolean_strings_are_normalized(self):
        self.write_gating({
            "enabled": "false",
            "trading_unlocked": "yes",
            "lock_winrate": "65",
            "min_usd": "12.5",
            "pumpfun_mcap_target": "250000",
            "token_mint": "  mint-address  ",
            "admin_wallets": [VALID_WALLET],
        })

        g = auth.gating()

        self.assertIs(g["enabled"], False)
        self.assertIs(g["trading_unlocked"], True)
        self.assertEqual(g["lock_winrate"], 65)
        self.assertEqual(g["min_usd"], 12.5)
        self.assertEqual(g["pumpfun_mcap_target"], 250000)
        self.assertEqual(g["token_mint"], "mint-address")
        self.assertEqual(g["admin_wallets"], {VALID_WALLET})

    def test_invalid_values_fall_back_to_defaults(self):
        self.write_gating({
            "enabled": "sometimes",
            "trading_unlocked": object().__class__.__name__,
            "lock_winrate": "-1",
            "min_usd": ["5"],
            "pumpfun_mcap_target": "nan",
            "admin_wallets": "not-a-wallet-list",
        })

        g = auth.gating()

        self.assertIs(g["enabled"], auth._DEFAULT_GATING["enabled"])
        self.assertIs(g["trading_unlocked"], auth._DEFAULT_GATING["trading_unlocked"])
        self.assertEqual(g["lock_winrate"], auth._DEFAULT_GATING["lock_winrate"])
        self.assertEqual(g["min_usd"], auth._DEFAULT_GATING["min_usd"])
        self.assertEqual(g["pumpfun_mcap_target"], auth._DEFAULT_GATING["pumpfun_mcap_target"])
        self.assertEqual(g["admin_wallets"], set())

    def test_admin_wallets_accepts_only_collections_of_valid_base58_strings(self):
        self.write_gating({
            "admin_wallets": [
                VALID_WALLET,
                f" {VALID_WALLET} ",
                "not base58",
                "123",
                123,
                None,
            ],
        })

        self.assertEqual(auth.gating()["admin_wallets"], {VALID_WALLET})

    def test_admin_wallet_helper_accepts_only_list_tuple_or_set(self):
        self.assertEqual(auth._coerce_admin_wallets([VALID_WALLET]), {VALID_WALLET})
        self.assertEqual(auth._coerce_admin_wallets((VALID_WALLET,)), {VALID_WALLET})
        self.assertEqual(auth._coerce_admin_wallets({VALID_WALLET}), {VALID_WALLET})
        self.assertEqual(auth._coerce_admin_wallets(VALID_WALLET), set())

    def test_valid_native_config_values_preserve_public_behavior(self):
        self.write_gating({
            "enabled": False,
            "trading_unlocked": True,
            "lock_winrate": 42,
            "min_usd": 7,
            "pumpfun_mcap_target": 90000,
            "admin_wallets": (VALID_WALLET,),
        })

        g = auth.gating()

        self.assertIs(g["enabled"], False)
        self.assertIs(g["trading_unlocked"], True)
        self.assertEqual(g["lock_winrate"], 42)
        self.assertEqual(g["min_usd"], 7)
        self.assertEqual(g["pumpfun_mcap_target"], 90000)
        self.assertEqual(g["admin_wallets"], {VALID_WALLET})


if __name__ == "__main__":
    unittest.main()
