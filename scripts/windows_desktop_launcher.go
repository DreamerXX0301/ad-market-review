package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func fail(message string) {
	fmt.Fprintln(os.Stderr, message)
	fmt.Fprintln(os.Stderr, "Press Enter to close.")
	bufio.NewReader(os.Stdin).ReadString('\n')
	os.Exit(1)
}

func main() {
	executable, err := os.Executable()
	if err != nil {
		fail("Cannot locate the desktop preview.")
	}
	folder := filepath.Dir(executable)
	runtime := filepath.Join(folder, "runtime", "python.exe")
	if _, err := os.Stat(runtime); err != nil {
		fail("Extract the entire ZIP, then open AMR-Desktop-Preview.exe in the extracted folder.")
	}
	args := append([]string{"-B", "-X", "utf8", "-m", "desktop_preview"}, os.Args[1:]...)
	command := exec.Command(runtime, args...)
	command.Dir = folder
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		fail("The desktop preview exited with an error. Save the diagnostic above.")
	}
}
