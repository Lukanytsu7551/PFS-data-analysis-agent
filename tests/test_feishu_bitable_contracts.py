import unittest
from unittest.mock import patch

from data.feishu_bitable_service import (
    FeishuBitableError,
    create_bitable,
    list_bitable_tables,
    parse_bitable_reference,
    read_bitable_records,
)
from data.sources.feishu_bitable import FeishuBitableDataSource


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self._body


class FeishuBitableContractTests(unittest.TestCase):
    def test_reference_validation_and_rich_cell_conversion_are_bounded(self):
        self.assertEqual(
            ("app_token", "tbl_main"),
            parse_bitable_reference(
                "https://feishu.cn/base/app_token?table=tbl_main"
            ),
        )
        with self.assertRaisesRegex(FeishuBitableError, "只支持飞书"):
            parse_bitable_reference("https://example.com/base/app_token")

        with patch(
            "data.feishu_bitable_service._credentials_and_token",
            return_value="tenant-token",
        ), patch(
            "data.feishu_bitable_service.requests.request",
            return_value=_Response(
                {
                    "data": {
                        "items": [
                            {
                                "record_id": "rec_1",
                                "fields": {
                                    "金额": "12.5",
                                    "负责人": [{"text": "Alice"}],
                                    "链接": {"url": "https://example.invalid"},
                                },
                            }
                        ],
                        "has_more": True,
                        "page_token": "next-page",
                    }
                }
            ),
        ) as request:
            result = read_bitable_records(
                bitable="https://feishu.cn/base/app_token?table=tbl_main",
                max_records=1,
            )

        self.assertEqual(1, result["record_count"])
        self.assertTrue(result["limited"])
        self.assertEqual(
            {
                "金额": "12.5",
                "负责人": "Alice",
                "链接": "https://example.invalid",
                "_feishu_record_id": "rec_1",
            },
            result["records"][0],
        )
        request.assert_called_once()
        self.assertEqual("Bearer tenant-token", request.call_args.kwargs["headers"]["Authorization"])

    def test_list_tables_paginates_with_stable_safe_links(self):
        responses = [
            _Response(
                {
                    "data": {
                        "items": [{"table_id": "tbl_1", "name": "销售"}],
                        "has_more": True,
                        "page_token": "page-2",
                    }
                }
            ),
            _Response(
                {
                    "data": {
                        "items": [{"table_id": "tbl_2", "name": "退款"}],
                        "has_more": False,
                    }
                }
            ),
        ]
        with patch(
            "data.feishu_bitable_service._credentials_and_token",
            return_value="tenant-token",
        ), patch(
            "data.feishu_bitable_service.requests.request",
            side_effect=responses,
        ) as request:
            result = list_bitable_tables(bitable="app_token")

        self.assertEqual(["tbl_1", "tbl_2"], [item["table_id"] for item in result["tables"]])
        self.assertEqual(
            "https://feishu.cn/base/app_token?table=tbl_2",
            result["tables"][1]["url"],
        )
        self.assertEqual(2, request.call_count)
        self.assertEqual("page-2", request.call_args_list[1].kwargs["params"]["page_token"])

    def test_read_snapshot_is_sql_analyzable_without_exposing_credentials(self):
        with patch(
            "data.feishu_bitable_service._credentials_and_token",
            return_value="tenant-token",
        ), patch(
            "data.feishu_bitable_service.requests.request",
            return_value=_Response(
                {
                    "data": {
                        "items": [
                            {"record_id": "rec_1", "fields": {"金额": "12.5", "城市": "华东"}},
                            {"record_id": "rec_2", "fields": {"金额": "7.5", "城市": "华南"}},
                        ],
                        "has_more": False,
                    }
                }
            ),
        ):
            loaded = read_bitable_records(
                bitable="app_token", table_id="tbl_main", max_records=500
            )

        source = FeishuBitableDataSource(loaded["records"], "销售快照", "sales")
        try:
            frame, error = source.execute_query('SELECT SUM("金额") AS total FROM "sales"')
            self.assertFalse(error)
            self.assertEqual(20.0, float(frame.iloc[0]["total"]))
            self.assertIn("feishu_record_id", source.get_schema())
            self.assertNotIn("tenant-token", source.get_schema())
        finally:
            source.close()

    def test_create_bitable_chunks_initial_records_and_accepts_nested_table_id(self):
        records = [{"城市": f"城市-{index}", "订单量": index} for index in range(150)]
        responses = [
            _Response({"data": {"app": {"app_token": "app_new", "url": "https://feishu.cn/base/app_new"}}}),
            _Response({"data": {"table": {"table_id": "tbl_new"}}}),
            _Response({"data": {}}),
            _Response({"data": {}}),
        ]
        with patch(
            "data.feishu_bitable_service._credentials_and_token",
            return_value="tenant-token",
        ), patch(
            "data.feishu_bitable_service._root_folder_token",
            return_value="folder-token",
        ), patch(
            "data.feishu_bitable_service.requests.request",
            side_effect=responses,
        ) as request:
            result = create_bitable(
                name="经营快照",
                table_name="城市订单",
                fields=["城市", "订单量"],
                records=records,
            )

        self.assertEqual("app_new", result["app_token"])
        self.assertEqual("tbl_new", result["table_id"])
        self.assertEqual(150, result["record_count"])
        self.assertEqual(4, request.call_count)
        self.assertEqual("folder-token", request.call_args_list[0].kwargs["json"]["folder_token"])
        self.assertEqual(100, len(request.call_args_list[2].kwargs["json"]["records"]))
        self.assertEqual(50, len(request.call_args_list[3].kwargs["json"]["records"]))

    def test_write_validation_rejects_duplicate_fields_and_unknown_record_fields(self):
        with self.assertRaisesRegex(FeishuBitableError, "字段名重复"):
            create_bitable(
                name="x",
                table_name="y",
                fields=["城市", "城市"],
            )
        with self.assertRaisesRegex(FeishuBitableError, "未定义字段"):
            create_bitable(
                name="x",
                table_name="y",
                fields=["城市"],
                records=[{"金额": 1}],
            )


if __name__ == "__main__":
    unittest.main()
