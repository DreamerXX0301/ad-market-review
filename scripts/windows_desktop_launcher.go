// Windows notification-area launcher for the private desktop preview.
package main

import (
	"bufio"
	"crypto/sha256"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	wmDestroy      = 0x0002
	wmClose        = 0x0010
	wmEndSession   = 0x0016
	wmTray         = 0x8001
	wmOpen         = 0x8002
	wmBrowser      = 0x8003
	wmExited       = 0x8004
	menuOpen       = 1
	menuQuit       = 2
	createNoWindow = 0x08000000
)

var (
	user32      = syscall.NewLazyDLL("user32.dll")
	shell32     = syscall.NewLazyDLL("shell32.dll")
	kernel32    = syscall.NewLazyDLL("kernel32.dll")
	ole32       = syscall.NewLazyDLL("ole32.dll")
	postMessage = user32.NewProc("PostMessageW")
	notifyIcon  = shell32.NewProc("Shell_NotifyIconW")
)

type windowClass struct {
	style                       uint32
	callback                    uintptr
	classExtra, windowExtra     int32
	instance, icon, cursor      uintptr
	background, menu, className uintptr
}

type point struct{ x, y int32 }

type message struct {
	window         uintptr
	id             uint32
	wParam, lParam uintptr
	time           uint32
	point          point
	private        uint32
}

type notification struct {
	size                uint32
	window              uintptr
	id, flags, callback uint32
	icon                uintptr
	tip                 [128]uint16
	state, stateMask    uint32
	info                [256]uint16
	version             uint32
	infoTitle           [64]uint16
	infoFlags           uint32
	guid                [16]byte
	balloonIcon         uintptr
}

type tray struct {
	window, taskbar uintptr
	icon            notification
	commands        io.WriteCloser
	openRequests    chan struct{}
	browserRequests chan string
	result          chan string
	stopping        bool
}

func wide(value string) *uint16 {
	value = strings.ReplaceAll(value, "\x00", "")
	pointer, _ := syscall.UTF16PtrFromString(value)
	return pointer
}

func dialog(message string, flags uintptr) {
	user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(wide(message))),
		uintptr(unsafe.Pointer(wide("冒险的梦想家"))), flags)
}

func pythonCommand(folder string, args ...string) *exec.Cmd {
	options := append([]string{"-B", "-X", "utf8", "-m", "desktop_preview"}, args...)
	command := exec.Command(filepath.Join(folder, "runtime", "python.exe"), options...)
	command.Dir = folder
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	return command
}

func validBrowserURL(raw string) bool {
	address, err := url.Parse(raw)
	if err != nil || address.Scheme != "http" || address.User != nil ||
		address.Hostname() != "127.0.0.1" || address.Path != "/__amr/open" || address.Fragment != "" {
		return false
	}
	port, err := strconv.Atoi(address.Port())
	ticket := address.Query().Get("ticket")
	return err == nil && port >= 1024 && port <= 65535 && len(ticket) == 43 &&
		address.RawQuery == "ticket="+ticket &&
		strings.IndexFunc(ticket, func(c rune) bool {
			return !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-')
		}) == -1
}

func (app *tray) readOutput(output io.Reader, command *exec.Cmd) {
	scanner := bufio.NewScanner(output)
	var tail string
	for scanner.Scan() {
		line := scanner.Text()
		if raw, ok := strings.CutPrefix(line, "AMR_OPEN "); ok {
			if validBrowserURL(raw) {
				select {
				case app.browserRequests <- raw:
					postMessage.Call(app.window, wmBrowser, 0, 0)
				default:
				}
			}
			continue
		}
		tail += line + "\n"
		if len(tail) > 4096 {
			tail = tail[len(tail)-4096:]
		}
	}
	_, _ = io.Copy(io.Discard, output)
	result := ""
	if err := command.Wait(); err != nil {
		result = strings.TrimSpace(tail)
		if result == "" {
			result = err.Error()
		}
	}
	app.result <- result
	postMessage.Call(app.window, wmExited, 0, 0)
}

func (app *tray) start(folder string) error {
	command := pythonCommand(folder)
	input, err := command.StdinPipe()
	if err != nil {
		return err
	}
	output, err := command.StdoutPipe()
	if err != nil {
		input.Close()
		return err
	}
	command.Stderr = command.Stdout
	if err := command.Start(); err != nil {
		input.Close()
		output.Close()
		return err
	}
	app.commands = input
	go func() {
		for range app.openRequests {
			if _, err := io.WriteString(input, "o"); err != nil {
				return
			}
		}
	}()
	go app.readOutput(output, command)
	return nil
}

func (app *tray) updateIcon(operation uintptr, title string) bool {
	app.icon.tip = [128]uint16{}
	copy(app.icon.tip[:], syscall.StringToUTF16(title))
	ok, _, _ := notifyIcon.Call(operation, uintptr(unsafe.Pointer(&app.icon)))
	return ok != 0
}

func (app *tray) open() {
	if !app.stopping {
		select {
		case app.openRequests <- struct{}{}:
		default:
		}
	}
}

func (app *tray) stop() {
	if app.stopping {
		return
	}
	app.stopping = true
	app.updateIcon(1, "冒险的梦想家 · 正在退出")
	close(app.openRequests)
	if app.commands != nil {
		app.commands.Close()
	}
}

func (app *tray) menu() {
	menu, _, _ := user32.NewProc("CreatePopupMenu").Call()
	if menu == 0 {
		return
	}
	defer user32.NewProc("DestroyMenu").Call(menu)
	appendMenu := user32.NewProc("AppendMenuW")
	if app.stopping {
		appendMenu.Call(menu, 1, 0, uintptr(unsafe.Pointer(wide("正在等待后台任务收尾"))))
	} else {
		appendMenu.Call(menu, 0, menuOpen, uintptr(unsafe.Pointer(wide("打开工作台"))))
		appendMenu.Call(menu, 0, menuQuit, uintptr(unsafe.Pointer(wide("退出工作台"))))
	}
	var cursor point
	user32.NewProc("GetCursorPos").Call(uintptr(unsafe.Pointer(&cursor)))
	user32.NewProc("SetForegroundWindow").Call(app.window)
	choice, _, _ := user32.NewProc("TrackPopupMenu").Call(
		menu, 0x0100|0x0080|0x0002, uintptr(cursor.x), uintptr(cursor.y), 0, app.window, 0)
	postMessage.Call(app.window, 0, 0, 0)
	switch choice {
	case menuOpen:
		app.open()
	case menuQuit:
		app.stop()
	}
}

func (app *tray) handle(window uintptr, id uint32, wParam, lParam uintptr) uintptr {
	switch id {
	case wmTray:
		switch uint32(lParam) {
		case 0x0203:
			app.open()
		case 0x0205:
			app.menu()
		}
	case wmOpen:
		app.open()
	case wmBrowser:
		var raw string
		select {
		case raw = <-app.browserRequests:
		default:
			return 0
		}
		if !app.stopping {
			result, _, _ := shell32.NewProc("ShellExecuteW").Call(
				window, 0, uintptr(unsafe.Pointer(wide(raw))), 0, 0, 1)
			if result <= 32 {
				dialog("无法用默认浏览器打开工作台。请检查 Windows 的默认浏览器设置，再从托盘重试。", 0x10)
			}
		}
	case wmExited:
		var result string
		select {
		case result = <-app.result:
		default:
			return 0
		}
		app.stop()
		if result != "" {
			dialog("工作台预览已停止。\n\n"+result, 0x10)
		}
		user32.NewProc("DestroyWindow").Call(window)
	case wmClose:
		app.stop()
	case wmEndSession:
		if wParam != 0 {
			app.stop()
		}
	case wmDestroy:
		notifyIcon.Call(2, uintptr(unsafe.Pointer(&app.icon)))
		user32.NewProc("PostQuitMessage").Call(0)
	default:
		if id != 0 && uintptr(id) == app.taskbar {
			title := "冒险的梦想家"
			if app.stopping {
				title += " · 正在退出"
			}
			app.updateIcon(0, title)
			return 0
		}
		result, _, _ := user32.NewProc("DefWindowProcW").Call(window, uintptr(id), wParam, lParam)
		return result
	}
	return 0
}

func runTray(folder, profile string) error {
	// ShellExecute can invoke a browser association via a COM shell extension.
	initialized, _, _ := ole32.NewProc("CoInitializeEx").Call(0, 2|4)
	if int32(initialized) >= 0 {
		defer ole32.NewProc("CoUninitialize").Call()
	}
	name := "AMRDesktopTray-" + profile
	mutex, _, lockError := kernel32.NewProc("CreateMutexW").Call(
		0, 0, uintptr(unsafe.Pointer(wide("Local\\"+name))))
	if mutex == 0 {
		return lockError
	}
	defer syscall.CloseHandle(syscall.Handle(mutex))
	if lockError == syscall.Errno(183) { // ERROR_ALREADY_EXISTS
		findWindow := user32.NewProc("FindWindowW")
		for range 30 {
			window, _, _ := findWindow.Call(uintptr(unsafe.Pointer(wide(name))), 0)
			if window != 0 {
				postMessage.Call(window, wmOpen, 0, 0)
				return nil
			}
			time.Sleep(100 * time.Millisecond)
		}
		return fmt.Errorf("工作台正在启动或退出，请稍后从托盘打开")
	}

	app := &tray{
		openRequests: make(chan struct{}, 1), browserRequests: make(chan string, 8),
		result: make(chan string, 1),
	}
	app.taskbar, _, _ = user32.NewProc("RegisterWindowMessageW").Call(
		uintptr(unsafe.Pointer(wide("TaskbarCreated"))))
	instance, _, _ := kernel32.NewProc("GetModuleHandleW").Call(0)
	className := wide(name)
	class := windowClass{
		callback: syscall.NewCallback(app.handle), instance: instance,
		className: uintptr(unsafe.Pointer(className)),
	}
	atom, _, err := user32.NewProc("RegisterClassW").Call(uintptr(unsafe.Pointer(&class)))
	if atom == 0 {
		return err
	}
	window, _, err := user32.NewProc("CreateWindowExW").Call(
		0, uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(wide("冒险的梦想家"))),
		0, 0, 0, 0, 0, 0, 0, instance, 0)
	runtime.KeepAlive(className)
	if window == 0 {
		return err
	}
	app.window = window
	icon, _, _ := user32.NewProc("LoadIconW").Call(0, 32512) // IDI_APPLICATION
	app.icon = notification{window: window, id: 1, flags: 7, callback: wmTray, icon: icon}
	app.icon.size = uint32(unsafe.Sizeof(app.icon))
	if !app.updateIcon(0, "冒险的梦想家") {
		return fmt.Errorf("无法创建托盘图标，请确认 Windows 任务栏正在运行")
	}
	if err := app.start(folder); err != nil {
		notifyIcon.Call(2, uintptr(unsafe.Pointer(&app.icon)))
		return err
	}
	getMessage := user32.NewProc("GetMessageW")
	dispatch := user32.NewProc("DispatchMessageW")
	var msg message
	for {
		result, _, err := getMessage.Call(uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
		if int32(result) == -1 {
			app.stop()
			<-app.result
			return err
		}
		if result == 0 {
			return nil
		}
		dispatch.Call(uintptr(unsafe.Pointer(&msg)))
	}
}

func main() {
	runtime.LockOSThread()
	executable, err := os.Executable()
	if err != nil {
		dialog("无法定位工作台启动器。", 0x10)
		return
	}
	folder := filepath.Dir(executable)
	if _, err := os.Stat(filepath.Join(folder, "runtime", "python.exe")); err != nil {
		dialog("请完整解压 ZIP，再打开 AMR-Desktop-Preview.exe。", 0x10)
		return
	}
	if len(os.Args) > 1 {
		if len(os.Args) != 2 || (os.Args[1] != "--diagnose" && os.Args[1] != "--import-previous") {
			dialog("可用命令为 --diagnose 和 --import-previous。", 0x10)
			return
		}
		output, err := pythonCommand(folder, os.Args[1]).CombinedOutput()
		if err != nil {
			dialog("工作台检查未完成。\n\n"+strings.TrimSpace(string(output)), 0x10)
			return
		}
		dialog(strings.TrimSpace(string(output)), 0x40)
		return
	}
	localAppData := os.Getenv("LOCALAPPDATA")
	if !filepath.IsAbs(localAppData) {
		dialog("无法读取 Windows 的本地应用数据目录。", 0x10)
		return
	}
	profile := sha256.Sum256([]byte(strings.ToLower(filepath.Clean(localAppData))))
	if err := runTray(folder, fmt.Sprintf("%x", profile[:10])); err != nil {
		dialog("工作台预览启动失败。\n\n"+err.Error(), 0x10)
	}
}
