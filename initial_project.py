import os
from pathlib import Path


def create_structure():
    # 获取当前运行脚本的所在目录作为项目根目录
    base_dir = Path(__file__).parent.resolve()
    print(f"🚀 开始在 {base_dir} 构建回测引擎基础骨架...")

    # 1. 定义需要创建的文件夹目录
    directories = [
        "data_feed",  # 板块一：数据路由与对齐层
        "broker",  # 板块二：撮合引擎层
        "portfolio",  # 板块三：账户与持仓管理层
        "strategy",  # 板块四：策略基类与具体策略
        "analyzer",  # 板块五：绩效分析与评价层
        "cache_data",  # 本地缓存池 (跑完后本地生成的 parquet)
        "tests"  # 单元测试目录
    ]

    for d in directories:
        dir_path = base_dir / d
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"📁 创建目录: {d}/")

    # 2. 定义需要创建的文件及其初始内容
    files_to_create = {
        "config.py": """# ==========================================
# 全局静态配置中心 (Config)
# 作用：隔离环境，存储数据库连接、常量、字典
# ==========================================
import os

# ClickHouse 数据库配置
CH_HOST = '192.168.100.23'  # 内网 IP
CH_USER = 'default'
CH_PASS = ''
CH_DB = 'adjusted_im_1_5_min'

# 缓存路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'cache_data')

# 手续费与滑点配置 (预留)
COMMISSION_DICT = {}
""",
        "data_feed/__init__.py": "",
        "data_feed/ch_loader.py": """# 负责直接与 ClickHouse 交互，拉取并缓存数据\n""",
        "data_feed/aligner.py": """# 负责多品种的时间轴对齐与向前填充 (ffill)\n""",
        "data_feed/data_provider.py": """# 数据层的统一出口，供外部 import 调用\n""",

        "broker/__init__.py": "",
        "portfolio/__init__.py": "",
        "strategy/__init__.py": "",
        "analyzer/__init__.py": "",
        ".gitignore": """__pycache__/
*.parquet
cache_data/
.idea/
.vscode/
"""
    }

    for file_path, content in files_to_create.items():
        full_path = base_dir / file_path
        if not full_path.exists():
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"📄 创建文件: {file_path}")
        else:
            print(f"⚠️ 文件已存在，跳过: {file_path}")

    print("\n✅ 回测引擎结构初始化完成！")


if __name__ == "__main__":
    create_structure()