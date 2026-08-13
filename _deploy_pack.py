# -*- coding: utf-8 -*-
"""Novel Ad Factory 服务器部署打包脚本
包含：代码 + .git（用于后续 git pull）+ 数据库 + 字体 + 配置
排除：meta_creatives 缓存、开发工具目录、输出目录
"""
import os
import shutil
import subprocess
import datetime

root = os.path.dirname(os.path.abspath(__file__))
os.chdir(root)

tmp = "_deploy_pack"
if os.path.exists(tmp):
    shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp)

# 1. 复制核心代码文件
core_files = [
    "main.py", "database.py", "scraper.py", "analytics.py", "delivery.py",
    "meta_api.py", "test_meta_creds.py", "test_meta_creds.bat",
    "config.json", "requirements.txt", "templates_index.json", "video_styles.json",
    ".gitignore", "一键启动.bat", "更新.bat", "start.sh", "update.sh",
]
for f in core_files:
    if os.path.exists(f):
        shutil.copy2(f, os.path.join(tmp, f))
        print(f"  文件: {f}")

# 2. 复制 .git（用于后续 git pull 更新）
if os.path.exists(".git"):
    shutil.copytree(".git", os.path.join(tmp, ".git"))
    print("  目录: .git (git 仓库)")

# 3. 复制数据（数据库）
if os.path.exists("data"):
    shutil.copytree("data", os.path.join(tmp, "data"))
    print("  目录: data (数据库)")

# 4. 复制字体
if os.path.exists("ziti"):
    shutil.copytree("ziti", os.path.join(tmp, "ziti"))
    print("  目录: ziti (字体)")

# 5. 复制提示词规则
if os.path.exists("prompts"):
    shutil.copytree("prompts", os.path.join(tmp, "prompts"))
    print("  目录: prompts")

# 6. 复制脚本工具
if os.path.exists("scripts"):
    shutil.copytree("scripts", os.path.join(tmp, "scripts"))
    print("  目录: scripts")

# 7. 复制 static（排除 meta_creatives 缓存，453M 可重新同步下载）
shutil.copytree(
    "static", os.path.join(tmp, "static"),
    ignore=shutil.ignore_patterns("meta_creatives")
)
print("  目录: static (已排除 meta_creatives 缓存)")

# 8. 压缩成 zip（用 PowerShell 处理中文文件名）
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
zip_name = f"NovelAdFactory_deploy_{timestamp}.zip"
print(f"\n正在压缩为 {zip_name} ...")
subprocess.run([
    "powershell", "-Command",
    f"Compress-Archive -Path '{tmp}\\*' -DestinationPath '{zip_name}' -Force"
], check=True)

# 9. 清理临时目录
shutil.rmtree(tmp, ignore_errors=True)

size_mb = os.path.getsize(zip_name) / 1024 / 1024
print(f"\n打包完成: {zip_name} ({size_mb:.1f} MB)")
print("上传到新服务器后解压，运行 一键启动.bat")
print("后续代码更新：在服务器目录执行 git pull")
