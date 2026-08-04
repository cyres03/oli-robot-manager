# Oli Robot Manager GitHub 协作说明

## 0. 仓库信息

| 项目 | 内容 |
|------|------|
| 仓库名称 | `cyres03/oli-robot-manager` |
| 仓库地址 | [https://github.com/cyres03/oli-robot-manager](https://github.com/cyres03/oli-robot-manager) |
| 可见性 | Private，仅受邀成员可访问 |
| 负责人 | `cyres03` |
| 默认分支 | `main` |
| 基线提交 | `5127507`，提交邮箱 `1641650362@qq.com` |

仓库已于 2026-08-04 完成首次上传，远程 `main` 已包含基线提交 `5127507`。当前开发电脑因无法连接 `github.com:443`，使用 GitHub 官方 SSH 备用入口 `ssh.github.com:443` 推送；这不影响同事在正常网络下使用 HTTPS。

---

## 1. 仓库负责人邀请同事

1. 请同事提供 **GitHub 用户名**，不是邮箱或昵称。
2. 打开 [Oli Robot Manager 私有仓库](https://github.com/cyres03/oli-robot-manager)。
3. 进入 `Settings` -> `Collaborators` -> `Add people`。
4. 输入同事的 GitHub 用户名并发送邀请。
5. 同事需要在 GitHub 通知或邮箱中接受邀请，之后才能访问私有仓库。

机器人密码不要写进邀请消息或 GitHub Issue，应通过公司内部私聊单独发送。

邀请状态为 `Pending` 时，即使已经设置 `Write` 权限，同事仍不能读取仓库。受邀人可以登录自己的 GitHub 账号后打开：

```text
https://github.com/cyres03/oli-robot-manager/invitations
```

---

## 2. 同事首次获取源码

先安装 Git，然后使用仓库 HTTPS 地址：

```powershell
git clone https://github.com/cyres03/oli-robot-manager.git
cd oli-robot-manager
```

如果当前网络无法连接 `github.com:443`，可以使用 GitHub 官方 SSH 443 备用入口。先把自己的 SSH 公钥添加到 GitHub 账号，再克隆：

1. 打开 `https://github.com/settings/ssh/new`
2. Title 填自己的电脑名称
3. Key type 选择 `Authentication Key`
4. 粘贴以 `ssh-ed25519` 开头的公钥并保存

```powershell
git clone ssh://git@ssh.github.com:443/cyres03/oli-robot-manager.git
cd oli-robot-manager
```

首次连接时，仅当 GitHub Ed25519 主机指纹如下时确认：

```text
SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
```

SSH 公钥应添加到公钥所有者自己的 GitHub 账号，不要添加为仓库 Deploy Key。负责人不能代替协作者把个人公钥添加到其账号。不要共享任何人的 SSH 私钥；每位开发者使用自己的 GitHub 账号和 SSH 密钥。

每位开发者应使用自己的姓名和 GitHub 已验证邮箱：

```powershell
git config user.name "自己的GitHub用户名"
git config user.email "自己的GitHub已验证邮箱"
```

安装运行环境：

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .\config.example.json .\config.local.json
```

将负责人私下提供的机器人连接参数填入 `config.local.json`，然后启动：

```powershell
python main.py
```

需要 Backlash 功能时执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_backlash_resource.ps1
```

`resources/backlash/backlash_install.zip` 约 113.7 MB，超过 GitHub 普通仓库 100 MB 单文件上限，因此不进入 Git 历史，而是通过私有 Release [Backlash Runtime Resource v1](https://github.com/cyres03/oli-robot-manager/releases/tag/backlash-resource-v1) 分发。

附件完整性：

```text
SHA-256: BACC27196221226AFE5339F3A47C9E492C565327DA0E454A7B223370E32A58EE
```

---

## 3. 每项开发工作的标准流程

开始前同步主分支：

```powershell
git switch main
git pull
```

创建功能分支，名称使用英文短横线：

```powershell
git switch -c feature/new-robot-identity
```

修改完成后检查：

```powershell
git status
python -m compileall -q .
python -m pytest -q
```

如果当前项目尚无对应自动化测试，应在 Pull Request 中写清楚实际完成的手工验证，不要写“测试通过”。

提交并推送：

```powershell
git add .
git commit -m "feat: support new robot identity"
git push -u origin feature/new-robot-identity
```

然后在 GitHub 页面点击 `Compare & pull request`，填写：

- 改了什么
- 为什么修改
- 如何验证
- 是否影响现有 Oli 机器人
- 是否涉及真实机器人动作和安全风险

由另一位开发者检查后再合并到 `main`。

---

## 4. 日常同步

自己的分支开发期间，定期同步主分支：

```powershell
git fetch origin
git merge origin/main
```

出现冲突时不要直接删除不理解的代码。先与修改同一文件的同事确认，再解决冲突并重新验证。

---

## 5. 禁止提交的内容

- `config.local.json`
- `.env` 和任何密码、令牌、私钥
- `.venv/`
- `build/`、`dist/`、`release_packages/`
- `installer/Output/`
- `__pycache__/`、`*.pyc`、`*.log`
- 本地数据库和机器人下载日志
- `resources/backlash/backlash_install.zip`

提交前用 `git status` 检查文件清单。发现凭据已经提交时，不要只删除文件后继续推送，应立即通知仓库负责人撤销凭据并清理 Git 历史。

---

## 6. 推荐的首轮分工

| 分支 | 工作内容 | 验收结果 |
|------|----------|----------|
| `feature/robot-profile` | 建立机器人型号与能力配置 | Oli 配置不回归，新型号可被选择 |
| `feature/new-robot-identity` | SSID、序列号和型号识别 | 两种机器人均能正确识别 |
| `feature/new-robot-adapter` | 新协议、状态和安全停止 | 状态可读，停止命令真机有效 |
| `feature/new-robot-acceptance` | 新型号验收项和阈值 | 自动验收使用正确地址与标准 |

首轮不要多人同时大改 `config.py`、`app.py` 和 `ui/main_window.py`。先约定接口和负责人，可以明显减少合并冲突。