import sys
import os
import subprocess
import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget, QPushButton,
    QTextEdit, QLineEdit, QComboBox, QLabel, QHBoxLayout, QProgressBar
)
from PyQt5.QtCore import Qt


class CodeMaster(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jadiv Code Master")
        self.setGeometry(100, 100, 900, 700)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # GitHub username to fetch repos
        self.github_username = "jan-tdy"

        # Initialize tabs
        self.init_code_runner()
        self.init_github_downloader()
        self.init_program_menu()

    # 1. Editor a spúšťač kódov
    def init_code_runner(self):
        code_tab = QWidget()
        layout = QVBoxLayout()

        self.code_editor = QTextEdit()
        self.code_editor.setPlaceholderText("Napíšte alebo vložte kód sem...")
        layout.addWidget(self.code_editor)

        self.run_button = QPushButton("Spustiť kód")
        self.run_button.clicked.connect(self.run_code)
        layout.addWidget(self.run_button)

        self.output_label = QLabel("Výstup:")
        layout.addWidget(self.output_label)

        self.code_output = QTextEdit()
        self.code_output.setReadOnly(True)
        layout.addWidget(self.code_output)

        code_tab.setLayout(layout)
        self.tabs.addTab(code_tab, "Editor & Spúšťač")

    def run_code(self):
        code = self.code_editor.toPlainText()
        temp_file = "/tmp/temp_code.py"

        try:
            with open(temp_file, "w") as f:
                f.write(code)

            process = subprocess.run(
                ["python3", temp_file],
                capture_output=True,
                text=True
            )
            self.code_output.setText(process.stdout + process.stderr)
        except Exception as e:
            self.code_output.setText(str(e))

    # 2. Downloader z GitHubu
    def init_github_downloader(self):
        github_tab = QWidget()
        layout = QVBoxLayout()

        self.github_url_input = QLineEdit()
        self.github_url_input.setPlaceholderText("Vložte URL GitHub súboru alebo repozitára...")
        layout.addWidget(self.github_url_input)

        self.download_button = QPushButton("Stiahnuť")
        self.download_button.clicked.connect(self.download_from_github)
        layout.addWidget(self.download_button)

        self.github_status_label = QLabel("Stav: Čakám na akciu...")
        layout.addWidget(self.github_status_label)

        github_tab.setLayout(layout)
        self.tabs.addTab(github_tab, "GitHub Downloader")

    def download_from_github(self):
        url = self.github_url_input.text().strip()
        if not url:
            self.github_status_label.setText("Chyba: URL nesmie byť prázdne.")
            return

        try:
            if url.endswith(".git"):
                repo_name = url.split("/")[-1].replace(".git", "")
                subprocess.run(["git", "clone", url], check=True)
                self.github_status_label.setText(f"Repo {repo_name} úspešne stiahnuté.")
            else:
                response = requests.get(url)
                file_name = url.split("/")[-1]
                with open(file_name, "wb") as file:
                    file.write(response.content)
                self.github_status_label.setText(f"Súbor {file_name} úspešne stiahnutý.")
        except Exception as e:
            self.github_status_label.setText(f"Chyba: {str(e)}")

    # 3. Menu programov na GitHube
    def init_program_menu(self):
        menu_tab = QWidget()
        layout = QVBoxLayout()

        self.repo_list = QComboBox()
        self.update_repo_list()
        layout.addWidget(self.repo_list)

        self.clone_button = QPushButton("Stiahnuť/aktualizovať vybraný program")
        self.clone_button.clicked.connect(self.clone_or_update_repo)
        layout.addWidget(self.clone_button)

        self.menu_status_label = QLabel("Stav: Čakám na akciu...")
        layout.addWidget(self.menu_status_label)

        menu_tab.setLayout(layout)
        self.tabs.addTab(menu_tab, "Menu programov")

    def update_repo_list(self):
        url = f"https://api.github.com/users/{self.github_username}/repos"
        try:
            response = requests.get(url)
            response.raise_for_status()
            repos = response.json()
            self.repo_list.clear()
            for repo in repos:
                self.repo_list.addItem(repo["name"])
        except Exception as e:
            self.repo_list.addItem("Chyba pri načítaní repo: " + str(e))

    def clone_or_update_repo(self):
        selected_repo = self.repo_list.currentText()
        if not selected_repo:
            self.menu_status_label.setText("Chyba: Žiadne repo nebolo vybraté.")
            return

        repo_url = f"https://github.com/{self.github_username}/{selected_repo}.git"
        local_path = os.path.join(os.getcwd(), selected_repo)

        try:
            if os.path.exists(local_path):
                subprocess.run(["git", "-C", local_path, "pull"], check=True)
                self.menu_status_label.setText(f"Repo {selected_repo} bolo aktualizované.")
            else:
                subprocess.run(["git", "clone", repo_url], check=True)
                self.menu_status_label.setText(f"Repo {selected_repo} bolo stiahnuté.")
        except Exception as e:
            self.menu_status_label.setText(f"Chyba: {str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CodeMaster()
    window.show()
    sys.exit(app.exec_())
