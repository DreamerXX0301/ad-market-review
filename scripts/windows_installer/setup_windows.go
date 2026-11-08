// Windows setup for the AMR desktop workspace.
package main

import (
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"unicode/utf16"
	"unsafe"
)

//go:embed payload.zip
var payload string

var payloadSHA256 string

var (
	setupUser32   = syscall.NewLazyDLL("user32.dll")
	setupShell32  = syscall.NewLazyDLL("shell32.dll")
	setupKernel32 = syscall.NewLazyDLL("kernel32.dll")
	setupOle32    = syscall.NewLazyDLL("ole32.dll")
	setupAdvapi32 = syscall.NewLazyDLL("advapi32.dll")
)

type browseInfo struct {
	owner, root        uintptr
	displayName, title *uint16
	flags              uint32
	callback, param    uintptr
	image              int32
}

func setupWide(value string) *uint16 {
	pointer, _ := syscall.UTF16PtrFromString(value)
	return pointer
}

func setupMessage(message string, flags uintptr) uintptr {
	result, _, _ := setupUser32.NewProc("MessageBoxW").Call(
		0, uintptr(unsafe.Pointer(setupWide(message))),
		uintptr(unsafe.Pointer(setupWide("冒险的梦想家 · 安装程序"))), flags)
	return result
}

func chooseParent() (string, error) {
	var displayName [260]uint16
	info := browseInfo{
		displayName: &displayName[0],
		title:       setupWide("选择本机安装目录，例如 D:\\。安装程序将在其中新建 AMR-Desktop。"),
		flags:       0x0001 | 0x0040, // BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
	}
	pidl, _, _ := setupShell32.NewProc("SHBrowseForFolderW").Call(uintptr(unsafe.Pointer(&info)))
	runtime.KeepAlive(info)
	if pidl == 0 {
		return "", nil
	}
	defer setupOle32.NewProc("CoTaskMemFree").Call(pidl)
	var path [260]uint16
	ok, _, _ := setupShell32.NewProc("SHGetPathFromIDListW").Call(pidl, uintptr(unsafe.Pointer(&path[0])))
	if ok == 0 {
		return "", errors.New("请选择本机磁盘上的普通文件夹")
	}
	return filepath.Clean(syscall.UTF16ToString(path[:])), nil
}

func trayMutex() (syscall.Handle, error) {
	local := os.Getenv("LOCALAPPDATA")
	if !filepath.IsAbs(local) {
		return 0, errors.New("无法读取 Windows 的本地应用数据目录")
	}
	profile := sha256.Sum256([]byte(strings.ToLower(filepath.Clean(local))))
	name := fmt.Sprintf("Local\\AMRDesktopTray-%x", profile[:10])
	handle, _, winErr := setupKernel32.NewProc("CreateMutexW").Call(0, 0, uintptr(unsafe.Pointer(setupWide(name))))
	if handle == 0 {
		return 0, fmt.Errorf("无法检查工作台是否运行：%w", winErr)
	}
	if winErr == syscall.Errno(183) { // ERROR_ALREADY_EXISTS
		syscall.CloseHandle(syscall.Handle(handle))
		return 0, errors.New("工作台或安装程序正在运行。请先从托盘退出工作台，等后台任务结束后再安装")
	}
	return syscall.Handle(handle), nil
}

func lockInstallParent(parent string) (func(), error) {
	volume := filepath.VolumeName(parent)
	if !filepath.IsAbs(parent) || len(volume) != 2 || volume[1] != ':' ||
		!strings.ContainsRune("ABCDEFGHIJKLMNOPQRSTUVWXYZ", rune(strings.ToUpper(volume)[0])) {
		return nil, errors.New("请选择带盘符的本机固定磁盘")
	}
	driveRoot := volume + "\\"
	driveType, _, _ := setupKernel32.NewProc("GetDriveTypeW").Call(uintptr(unsafe.Pointer(setupWide(driveRoot))))
	if driveType != 3 { // DRIVE_FIXED
		return nil, errors.New("请选择本机固定磁盘，例如 C: 或 D:")
	}
	var fsName [64]uint16
	ok, _, winErr := setupKernel32.NewProc("GetVolumeInformationW").Call(
		uintptr(unsafe.Pointer(setupWide(driveRoot))), 0, 0, 0, 0, 0,
		uintptr(unsafe.Pointer(&fsName[0])), uintptr(len(fsName)))
	if ok == 0 {
		return nil, fmt.Errorf("无法核验磁盘格式：%w", winErr)
	}
	if !strings.EqualFold(syscall.UTF16ToString(fsName[:]), "NTFS") {
		return nil, errors.New("请选择 NTFS 格式的本机固定磁盘")
	}

	paths := []string{parent}
	for path := parent; path != driveRoot; {
		if !validWindowsComponent(filepath.Base(path)) {
			return nil, errors.New("安装路径包含不受支持的文件夹名")
		}
		next := filepath.Dir(path)
		if next == path {
			return nil, errors.New("无法核验安装路径")
		}
		paths = append(paths, next)
		path = next
	}
	var handles []syscall.Handle
	closeHandles := func() {
		for _, handle := range handles {
			syscall.CloseHandle(handle)
		}
	}
	for i := len(paths) - 1; i >= 0; i-- {
		handle, err := syscall.CreateFile(
			setupWide(paths[i]), 0x80, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE, nil,
			syscall.OPEN_EXISTING, syscall.FILE_FLAG_BACKUP_SEMANTICS|0x00200000, 0)
		if err != nil {
			closeHandles()
			return nil, fmt.Errorf("无法打开安装目录 %s：%w", paths[i], err)
		}
		handles = append(handles, handle)
		var info syscall.ByHandleFileInformation
		if err := syscall.GetFileInformationByHandle(handle, &info); err != nil {
			closeHandles()
			return nil, fmt.Errorf("无法核验安装目录 %s：%w", paths[i], err)
		}
		if info.FileAttributes&syscall.FILE_ATTRIBUTE_DIRECTORY == 0 || info.FileAttributes&0x0400 != 0 {
			closeHandles()
			return nil, errors.New("安装路径必须是普通文件夹，不能经过链接、挂载点或目录联接")
		}
	}
	return closeHandles, nil
}

func availableBytes(path string) (uint64, error) {
	var available uint64
	ok, _, winErr := setupKernel32.NewProc("GetDiskFreeSpaceExW").Call(
		uintptr(unsafe.Pointer(setupWide(path))), uintptr(unsafe.Pointer(&available)), 0, 0)
	if ok == 0 {
		return 0, fmt.Errorf("无法检查所选磁盘的可用空间：%w", winErr)
	}
	return available, nil
}

func createPrivateStage(path string) error {
	token, err := syscall.OpenCurrentProcessToken()
	if err != nil {
		return err
	}
	defer token.Close()
	user, err := token.GetTokenUser()
	if err != nil {
		return err
	}
	sid, err := user.User.Sid.String()
	if err != nil {
		return err
	}
	// Disable inherited permissions from the selected parent. Descendants
	// inherit access for this Windows user and SYSTEM.
	sddl := "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;" + sid + ")"
	var descriptor uintptr
	ok, _, winErr := setupAdvapi32.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW").Call(
		uintptr(unsafe.Pointer(setupWide(sddl))), 1, uintptr(unsafe.Pointer(&descriptor)), 0)
	if ok == 0 {
		return winErr
	}
	defer setupKernel32.NewProc("LocalFree").Call(descriptor)
	security := syscall.SecurityAttributes{SecurityDescriptor: descriptor}
	security.Length = uint32(unsafe.Sizeof(security))
	return syscall.CreateDirectory(setupWide(path), &security)
}

type progressDialog struct {
	methods *[13]uintptr
}

var progressCLSID = syscall.GUID{Data1: 0xf8383852, Data2: 0xfcd3, Data3: 0x11d1, Data4: [8]byte{0xa6, 0xb9, 0, 0x60, 0x97, 0xdf, 0x5b, 0xd4}}
var progressIID = syscall.GUID{Data1: 0xebbc7c04, Data2: 0x315e, Data3: 0x11d2, Data4: [8]byte{0xb6, 0x2f, 0, 0x60, 0x97, 0xdf, 0x5b, 0xd4}}

func showInstallProgress(target string) (func(int, int) bool, func()) {
	var dialog *progressDialog
	result, _, _ := setupOle32.NewProc("CoCreateInstance").Call(
		uintptr(unsafe.Pointer(&progressCLSID)), 0, 1, uintptr(unsafe.Pointer(&progressIID)),
		uintptr(unsafe.Pointer(&dialog)))
	if int32(result) < 0 || dialog == nil {
		return func(int, int) bool { return true }, func() {}
	}
	self := uintptr(unsafe.Pointer(dialog))
	syscall.SyscallN(dialog.methods[5], self, uintptr(unsafe.Pointer(setupWide("正在安装冒险的梦想家"))))
	syscall.SyscallN(dialog.methods[3], self, 0, 0, 0, 0)
	syscall.SyscallN(dialog.methods[10], self, 1, uintptr(unsafe.Pointer(setupWide(target))), 1, 0)
	last := -1
	return func(done, total int) bool {
			if percent := done * 100 / total; percent != last {
				syscall.SyscallN(dialog.methods[9], self, uintptr(done), uintptr(total))
				last = percent
			}
			cancelled, _, _ := syscall.SyscallN(dialog.methods[7], self)
			return cancelled == 0
		}, func() {
			syscall.SyscallN(dialog.methods[4], self)
			syscall.SyscallN(dialog.methods[2], self)
		}
}

func installPayload(parent string, archive installationPayload) (target string, err error) {
	release, err := lockInstallParent(parent)
	if err != nil {
		return "", err
	}
	defer release()
	target = filepath.Join(parent, installDirectory)
	if _, err := os.Lstat(target); !errors.Is(err, os.ErrNotExist) {
		return "", errors.New("目标 AMR-Desktop 已存在或无法检查。请选另一个上级文件夹")
	}
	free, err := availableBytes(parent)
	if err != nil {
		return "", err
	}
	required := archive.bytes + (1 << 30)
	if free < required {
		return "", fmt.Errorf("所选磁盘空间不足。安装需要至少 %.2f GiB 可用空间，当前剩余 %.2f GiB",
			float64(required)/(1<<30), float64(free)/(1<<30))
	}
	if setupMessage("将安装到以下目录，并把业务数据和临时文件保存在同一个磁盘。\n\n"+target, 0x01|0x40) != 1 {
		return "", installationCancelled
	}

	staging := filepath.Join(parent, ".amr-setup-"+rand.Text()[:12])
	app := filepath.Join(staging, "app")
	for _, file := range archive.files {
		if len(utf16.Encode([]rune(filepath.Join(app, filepath.FromSlash(file.name))))) >= 259 {
			return "", errors.New("安装路径过长，请选择更靠近盘符根目录的文件夹")
		}
	}
	if err := createPrivateStage(staging); err != nil {
		return "", fmt.Errorf("无法创建安装目录。请选择当前 Windows 用户可写的文件夹：%w", err)
	}
	committed := false
	defer func() {
		if !committed {
			if removeErr := os.RemoveAll(staging); removeErr != nil {
				err = errors.Join(err, fmt.Errorf("临时目录 %s 未能清理：%w", staging, removeErr))
			}
		}
	}()

	progress, finish := showInstallProgress(target)
	defer finish()
	if err := extractPayload(archive, app, progress); err != nil {
		return "", err
	}
	ok, _, winErr := setupKernel32.NewProc("MoveFileExW").Call(
		uintptr(unsafe.Pointer(setupWide(staging))), uintptr(unsafe.Pointer(setupWide(target))), 8)
	if ok == 0 { // MOVEFILE_WRITE_THROUGH, without replacing an existing directory.
		return "", fmt.Errorf("安装目录未能启用：%w", winErr)
	}
	committed = true
	return target, nil
}

func main() {
	runtime.LockOSThread()
	initialized, _, _ := setupOle32.NewProc("CoInitializeEx").Call(0, 2|4)
	if int32(initialized) < 0 {
		setupMessage("无法打开 Windows 安装向导。", 0x10)
		return
	}
	defer setupOle32.NewProc("CoUninitialize").Call()

	mutex, err := trayMutex()
	if err != nil {
		setupMessage(err.Error(), 0x30)
		return
	}
	defer func() {
		if mutex != 0 {
			syscall.CloseHandle(mutex)
		}
	}()

	parent, err := chooseParent()
	if err != nil {
		setupMessage(err.Error(), 0x30)
		return
	}
	if parent == "" {
		return
	}
	archive, err := inspectPayload(strings.NewReader(payload), int64(len(payload)), payloadSHA256)
	if err != nil {
		setupMessage("无法验证安装文件。\n\n"+err.Error(), 0x10)
		return
	}
	target, err := installPayload(parent, archive)
	if err == installationCancelled {
		return
	}
	if err != nil {
		setupMessage("安装未完成。\n\n"+err.Error(), 0x10)
		return
	}

	app := filepath.Join(target, "app")
	launcher := filepath.Join(app, launcherName)
	syscall.CloseHandle(mutex)
	mutex = 0
	command := exec.Command(launcher)
	command.Dir = app
	if err := command.Start(); err != nil {
		setupMessage("安装已完成。请双击以下程序启动工作台。\n\n"+launcher, 0x30)
		return
	}
	_ = command.Process.Release()
}
