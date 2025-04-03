import os
import threading
import time
import json
import re
from tkinter import Tk, Label, Entry, Button, StringVar, messagebox, Toplevel, Text, Scrollbar
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime

CONFIG_FILE = "config.json"
firebase_app = None
database_ref = None
monitoring_thread = None
stop_monitoring_flag = False
log_window = None

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            return json.load(file)
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w") as file:
        json.dump(config, file, indent=4)

def initialize_firebase(url, key_path):
    global firebase_app, database_ref
    try:
        if firebase_app:
            firebase_admin.delete_app(firebase_app)

        cred = credentials.Certificate(key_path)
        firebase_app = firebase_admin.initialize_app(cred, {'databaseURL': url})
        database_ref = db.reference("/frequency")
        clear_firebase_data()
        log_message("Firebase ініціалізовано успішно!")
        return True
    except Exception as e:
        log_message(f"Помилка ініціалізації Firebase: {e}", error=True)
        return False

def clear_firebase_data():
    try:
        if database_ref:
            database_ref.delete()
            log_message("Базу даних Firebase очищено успішно!")
            return True
    except Exception as e:
        log_message(f"Помилка при очищенні бази даних: {e}", error=True)
        return False

def is_valid_folder_name(folder_name):
    return re.match(r"^\d{3}\.\d{3}$", folder_name) is not None

def log_message(message, error=False):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_text = f"[{timestamp}] {message}"

    if log_window and log_window.winfo_exists():
        log_window.text.configure(state='normal')
        tag = "error" if error else "info"
        log_window.text.insert('end', log_text + "\n", tag)
        log_window.text.configure(state='disabled')
        log_window.text.see('end')

    print(log_text)

def create_log_window():
    global log_window
    if log_window is None or not log_window.winfo_exists():
        log_window = Toplevel()
        log_window.title("Лог моніторингу")
        log_window.geometry("800x400")

        scrollbar = Scrollbar(log_window)
        scrollbar.pack(side="right", fill="y")

        log_window.text = Text(log_window, wrap="word", yscrollcommand=scrollbar.set)
        log_window.text.pack(expand=True, fill="both")

        log_window.text.tag_config("info", foreground="green")
        log_window.text.tag_config("error", foreground="red")
        log_window.text.configure(state='disabled')

        scrollbar.config(command=log_window.text.yview)

def extract_folder_info(folder_name):
    """
    Витягує інформацію про частоту з назви папки.
    Повертає частоту у форматі ***.*** або None, якщо формат невірний.
    """
    # Логуємо вхідну назву папки
    log_message(f"Отримано назву папки: '{folder_name}'", error=False)
    
    # Шукаємо послідовність з 3 цифр, роздільник (крапка/кома), і ще 3 цифри
    match = re.search(r"(\d{3})[.,](\d{3})", folder_name)
    
    if match:
        processed_name = f"{match.group(1)}.{match.group(2)}"
        # Логуємо оброблену назву
        log_message(f"Знайдено частоту: '{processed_name}' у папці '{folder_name}'", error=False)
        return processed_name
    # Логуємо невдалу спробу обробки
    log_message(f"Не вдалося визначити частоту в папці: '{folder_name}'", error=True)
    return None

def sync_with_firebase(directory_path):
    global stop_monitoring_flag
    tracked_folders = {}
    
    log_message(f"Початок моніторингу папки: {directory_path}", error=False)
    
    while not stop_monitoring_flag:
        try:
            current_folders = set(
                f for f in os.listdir(directory_path)
                if os.path.isdir(os.path.join(directory_path, f))
            )
            
            log_message(f"Знайдено {len(current_folders)} папок у директорії", error=False)
            
            # Обробка нових папок
            new_folders = current_folders - set(tracked_folders.keys())
            log_message(f"Виявлено {len(new_folders)} нових папок", error=False)
            
            for folder in new_folders:
                processed_name = extract_folder_info(folder)  # Змінено на extract_folder_info
                if processed_name:
                    full_path = os.path.join(directory_path, folder)
                    new_ref = database_ref.push()
                    data = {
                        'name': processed_name,
                        'original_name': folder,
                        'timestamp': time.time(),
                        'status': 'active',
                        'last_modified': os.path.getmtime(full_path)
                    }
                    new_ref.set(data)
                    tracked_folders[folder] = {
                        'path': full_path,
                        'firebase_key': new_ref.key,
                        'last_modified': os.path.getmtime(full_path)
                    }
                    log_message(f"Додано до Firebase: {processed_name} (оригінал: {folder})", error=False)

            # Обробка видалених папок
            deleted_folders = set(tracked_folders.keys()) - current_folders
            for folder in deleted_folders:
                if folder in tracked_folders:
                    database_ref.child(tracked_folders[folder]['firebase_key']).delete()
                    log_message(f"Видалено частоту: {folder}")
                    del tracked_folders[folder]

            # Перевірка змінених папок
            for folder, data in list(tracked_folders.items()):
                if folder in current_folders:
                    full_path = os.path.join(directory_path, folder)
                    current_mtime = os.path.getmtime(full_path)
                    if current_mtime > data['last_modified']:
                        database_ref.child(data['firebase_key']).update({
                            'last_modified': current_mtime,
                            'timestamp': time.time()
                        })
                        tracked_folders[folder]['last_modified'] = current_mtime
                        log_message(f"Оновлено часову мітку для: {folder}")

            time.sleep(5)

        except Exception as e:
            log_message(f"Помилка синхронізації: {e}", error=True)
            time.sleep(10)
def start_monitoring(directory_path, firebase_url, firebase_key_path):
    global monitoring_thread, stop_monitoring_flag

    if not directory_path or not os.path.isdir(directory_path):
        messagebox.showerror("Помилка", "Вкажіть коректний шлях до папки!")
        return False

    if not firebase_url or not firebase_key_path:
        messagebox.showerror("Помилка", "Вкажіть URL та шлях до ключа Firebase!")
        return False

    if not initialize_firebase(firebase_url, firebase_key_path):
        return False

    create_log_window()
    stop_monitoring_flag = False
    monitoring_thread = threading.Thread(
        target=sync_with_firebase,
        args=(directory_path,),
        daemon=True
    )
    monitoring_thread.start()
    log_message("Моніторинг запущено")
    return True

def stop_monitoring():
    global stop_monitoring_flag
    stop_monitoring_flag = True
    if monitoring_thread and monitoring_thread.is_alive():
        monitoring_thread.join(timeout=2)
    log_message("Моніторинг зупинено")
    messagebox.showinfo("Інформація", "Моніторинг зупинено")

def create_log_window(show=True):
    global log_window
    if show:
        if log_window is None or not log_window.winfo_exists():
            log_window = Toplevel()
            log_window.title("Лог моніторингу")
            log_window.geometry("800x400")

            scrollbar = Scrollbar(log_window)
            scrollbar.pack(side="right", fill="y")

            log_window.text = Text(log_window, wrap="word", yscrollcommand=scrollbar.set)
            log_window.text.pack(expand=True, fill="both")

            log_window.text.tag_config("info", foreground="green")
            log_window.text.tag_config("error", foreground="red")
            log_window.text.configure(state='disabled')

            scrollbar.config(command=log_window.text.yview)
    else:
        if log_window and log_window.winfo_exists():
            log_window.destroy()

def toggle_log_window():
    if log_window and log_window.winfo_exists():
        create_log_window(show=False)
    else:
        create_log_window(show=True)

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Folder-Firebase Sync")

        # Відкриваємо вікно логування при запуску
        create_log_window()

        self.config = load_config()

        self.firebase_url = StringVar(value=self.config.get("firebase_url", ""))
        self.firebase_key_path = StringVar(value=self.config.get("firebase_key_path", ""))
        self.directory_path = StringVar(value=self.config.get("directory_path", ""))

        # UI Elements
        Label(root, text="Firebase URL:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        Entry(root, textvariable=self.firebase_url, width=50).grid(row=0, column=1, padx=5, pady=5)

        Label(root, text="Шлях до ключа Firebase:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        Entry(root, textvariable=self.firebase_key_path, width=50).grid(row=1, column=1, padx=5, pady=5)
        Button(root, text="Огляд...", command=self.browse_key_file).grid(row=1, column=2, padx=5, pady=5)

        Label(root, text="Папка для моніторингу:").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        Entry(root, textvariable=self.directory_path, width=50).grid(row=2, column=1, padx=5, pady=5)
        Button(root, text="Огляд...", command=self.browse_directory).grid(row=2, column=2, padx=5, pady=5)

        Button(root, text="Почати моніторинг", command=self.start_monitoring).grid(row=3, column=1, pady=10)
        Button(root, text="Зупинити моніторинг", command=stop_monitoring).grid(row=4, column=1, pady=5)
        Button(root, text="Показати/сховати лог", command=toggle_log_window).grid(row=5, column=1, pady=5)

    def browse_key_file(self):
        from tkinter import filedialog
        key_path = filedialog.askopenfilename(title="Виберіть файл ключа Firebase")
        if key_path:
            self.firebase_key_path.set(key_path)

    def browse_directory(self):
        from tkinter import filedialog
        directory = filedialog.askdirectory(title="Виберіть папку для моніторингу")
        if directory:
            self.directory_path.set(directory)

    def start_monitoring(self):
        self.config.update({
            "firebase_url": self.firebase_url.get(),
            "firebase_key_path": self.firebase_key_path.get(),
            "directory_path": self.directory_path.get()
        })
        save_config(self.config)

        if start_monitoring(
            self.directory_path.get(),
            self.firebase_url.get(),
            self.firebase_key_path.get()
        ):
            messagebox.showinfo("Успіх", "Моніторинг запущено!")

if __name__ == "__main__":
    root = Tk()
    app = App(root)
    root.mainloop()