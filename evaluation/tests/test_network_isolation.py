from __future__ import annotations

import json
import unittest

from evaluation.common.inference_relay import (
    allowed_endpoint_paths,
    endpoint_allowed,
    upstream_headers,
    validate_inference_body,
)
from evaluation.common.network_isolation import (
    gateway_hosts,
    local_relay_base_url,
)


class NetworkIsolationTest(unittest.TestCase):
    def test_gateway_hosts_require_https_and_deduplicate(self) -> None:
        self.assertEqual(
            gateway_hosts(
                (
                    "https://YEYSAI.com/v1",
                    "https://yeysai.com/anthropic",
                )
            ),
            ("yeysai.com",),
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            gateway_hosts(("http://example.com",))
        with self.assertRaisesRegex(ValueError, "port 443"):
            gateway_hosts(("https://example.com:8443",))
        with self.assertRaisesRegex(ValueError, "not an IP address"):
            gateway_hosts(("https://192.0.2.1",))
        with self.assertRaisesRegex(ValueError, "credentials"):
            gateway_hosts(("https://user:pass@example.com/v1",))
        with self.assertRaisesRegex(ValueError, "query or fragment"):
            gateway_hosts(("https://example.com/v1?target=other",))

    def test_local_relay_mirrors_only_the_gateway_base_path(self) -> None:
        self.assertEqual(
            local_relay_base_url("https://yeysai.com/v1/"),
            "http://127.0.0.1:18080/v1",
        )

    def test_relay_accepts_only_exact_inference_paths(self) -> None:
        paths = allowed_endpoint_paths("/v1")
        self.assertTrue(endpoint_allowed("/v1/responses?stream=true", paths))
        self.assertTrue(endpoint_allowed("/v1/chat/completions", paths))
        self.assertTrue(endpoint_allowed("/v1/messages", paths))
        self.assertFalse(endpoint_allowed("/v1/models", paths))
        self.assertFalse(endpoint_allowed("/admin/v1/responses", paths))

    def test_relay_validates_model_and_rejects_web_tools(self) -> None:
        payload = {"model": "gpt-4o", "tools": [{"type": "function"}]}
        self.assertEqual(
            validate_inference_body(
                json.dumps(payload).encode(),
                {"gpt-4o"},
            ),
            payload,
        )
        with self.assertRaisesRegex(ValueError, "model is not allowed"):
            validate_inference_body(
                b'{"model":"other-model"}',
                {"gpt-4o"},
            )
        with self.assertRaisesRegex(ValueError, "model is not allowed"):
            validate_inference_body(b"{}", {"gpt-4o"})
        with self.assertRaisesRegex(ValueError, "network tools"):
            validate_inference_body(
                b'{"model":"gpt-4o","tools":[{"type":"web_search_preview"}]}',
                {"gpt-4o"},
            )
        with self.assertRaisesRegex(ValueError, "network tools"):
            validate_inference_body(
                b'{"model":"claude","tools":[{"type":"web_search_20250305"}]}',
                {"claude"},
            )
        with self.assertRaisesRegex(ValueError, "remote resources"):
            validate_inference_body(
                b'{"model":"gpt-4o","input":[{"image_url":"https://example.com/x"}]}',
                {"gpt-4o"},
            )
        self.assertEqual(
            validate_inference_body(
                b'{"model":"gpt-4o","input":"Please explain https://example.com"}',
                {"gpt-4o"},
            )["model"],
            "gpt-4o",
        )

    def test_relay_replaces_agent_authentication(self) -> None:
        headers = upstream_headers(
            {
                "Authorization": "Bearer dummy",
                "x-api-key": "dummy",
                "Content-Type": "application/json",
                "Host": "127.0.0.1",
            },
            upstream_host="yeysai.com",
            api_key="real-secret",
            auth_mode="bearer",
        )
        self.assertEqual(headers["Authorization"], "Bearer real-secret")
        self.assertNotIn("x-api-key", headers)
        self.assertEqual(headers["Host"], "yeysai.com")
        self.assertEqual(headers["Content-Type"], "application/json")


if __name__ == "__main__":
    unittest.main()
