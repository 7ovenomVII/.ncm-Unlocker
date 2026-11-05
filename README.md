# .ncm-Unlocker
用于将ncm文件解密输出为无加密的音频文件的小程序

# 目前为测试版本
以外包GUI封装+开源程序，我主要做的是提供一个操作界面降低上手门槛

# 关键功能实现
通过github开源程序ncmdump.exe实现

# 注意事项
使用PyInstaller打包onedir，必须保留依赖库文件，目前无法实现单个exe打包，会报错
使用时将 NCM_Studio_v0.01.zip 尽量放到纯英文路径（路径含中文可能会有bug），解压后即可运行 NCM Studio.exe