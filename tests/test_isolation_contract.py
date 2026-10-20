"""夹具契约：整套测试必须只使用临时目录，绝不能碰仓库里的真实数据库。"""
import os
import tempfile
import unittest

# 先经过测试夹具：它会在项目模块导入前设置临时库与临时目录。
from test_tasks import app_module  # noqa: F401
from common import DB_PATH


class IsolationContract(unittest.TestCase):
    def setUp(self):
        self.temp_root = os.path.abspath(tempfile.gettempdir())

    def assert_inside_temp(self, label, path):
        resolved = os.path.abspath(str(path))
        self.assertTrue(
            os.path.commonpath([resolved, self.temp_root]) == self.temp_root,
            f'{label} 指向临时目录之外：{resolved}',
        )

    def test_database_is_the_configured_temporary_file(self):
        configured = os.environ.get('PROGRESS_DB')
        self.assertIsNotNone(configured, 'PROGRESS_DB 必须在导入项目模块前设置')
        self.assertEqual(str(DB_PATH), str(configured))
        self.assert_inside_temp('测试数据库', DB_PATH)

    def test_data_directories_are_isolated(self):
        from modules import funds, invoices
        self.assert_inside_temp('资金目录', funds.FUNDS_DIR)
        self.assert_inside_temp('发票目录', invoices.BASE_DIR)

    def test_secret_key_is_ephemeral(self):
        import app as application
        self.assert_inside_temp('会话密钥', application.SECRET_PATH)


if __name__ == '__main__':
    unittest.main()
