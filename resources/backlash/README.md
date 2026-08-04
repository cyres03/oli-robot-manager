# Backlash 运行资源

`backlash_install.zip` 大于 GitHub 普通仓库的 100 MB 单文件上限，因此不直接提交到 Git。

有仓库访问权限的开发者克隆源码后，在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_backlash_resource.ps1
```

脚本会从本私有仓库的 `backlash-resource-v1` Release 下载文件到：

```text
resources\backlash\backlash_install.zip
```

该文件只用于运行 Backlash 回差检测，不影响主界面、基础控制、舞蹈动作库和其他验收功能的源码开发。