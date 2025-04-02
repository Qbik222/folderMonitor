import os
import threading
import time
import json
import re
from tkinter import Tk, Label, Entry, Button, StringVar, messagebox
import firebase_admin
from firebase_admin import credentials, db

CONFIG_FILE = "config.json"
firebase_app = None
database_ref = None
monitoring_thread = None
stop_monitoring_flag = False

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
        database_ref = db.reference("/frequency")  # Використовуємо окрему гілку для папок
        messagebox.showinfo("Успіх", "Firebase ініціалізовано успішно!")
        return True
    except Exception as e:
        messagebox.showerror("Помилка", f"Не вдалося ініціалізувати Firebase: {e}")
        return False

def is_valid_folder_name(folder_name):
    return re.match(r"^\d{3}\.\d{3}$", folder_name) is not None

def sync_with_firebase(directory_path):
    global stop_monitoring_flag

    # Словник для відстеження стану папок: {folder_name: {"path": full_path, "firebase_key": key}}
    tracked_folders = {}

    # Спочатку завантажуємо вже відстежувані папки з Firebase
    try:
        existing_folders = database_ref.get() or {}
        for key, value in existing_folders.items():
            if 'name' in value:
                folder_name = value['name']
                full_path = os.path.join(directory_path, folder_name)
                if os.path.exists(full_path):
                    tracked_folders[folder_name] = {
                        'path': full_path,
                        'firebase_key': key
                    }
    except Exception as e:
        print(f"Помилка при завантаженні існуючих папок: {e}")

    while not stop_monitoring_flag:
        try:
            # Отримуємо поточний список папок
            current_folders = set(
                f for f in os.listdir(directory_path)
                if os.path.isdir(os.path.join(directory_path, f)) and is_valid_folder_name(f)
            )

            # 1. Перевірка нових папок
            new_folders = current_folders - set(tracked_folders.keys())
            for folder in new_folders:
                full_path = os.path.join(directory_path, folder)
                # Додаємо нову папку до Firebase
                new_ref = database_ref.push()
                new_ref.set({
                    'name': folder,
                    'timestamp': time.time()
                })
                tracked_folders[folder] = {
                    'path': full_path,
                    'firebase_key': new_ref.key
                }
                print(f"Додано нову папку: {folder} (Firebase key: {new_ref.key})")

            # 2. Перевірка видалених папок
            deleted_folders = set(tracked_folders.keys()) - current_folders
            for folder in deleted_folders:
                if folder in tracked_folders:
                    # Видаляємо запис з Firebase
                    database_ref.child(tracked_folders[folder]['firebase_key']).delete()
                    print(f"Видалено папку: {folder}")
                    del tracked_folders[folder]

            # 3. Перевірка перейменованих папок
            for folder, data in list(tracked_folders.items()):
                if not os.path.exists(data['path']):
                    # Шукаємо папку з таким самим inode (перейменована)
                    new_name = None
                    for f in current_folders:
                        try:
                            if os.path.samefile(os.path.join(directory_path, f), data['path']):
                                new_name = f
                                break
                        except FileNotFoundError:
                            continue

                    if new_name and is_valid_folder_name(new_name):
                        # Оновлюємо запис у Firebase
                        database_ref.child(data['firebase_key']).update({
                            'name': new_name,
                            'timestamp': time.time()
                        })
                        print(f"Папку перейменовано: {folder} -> {new_name}")

                        # Оновлюємо словник відстежуваних папок
                        tracked_folders[new_name] = {
                            'path': os.path.join(directory_path, new_name),
                            'firebase_key': data['firebase_key']
                        }
                        del tracked_folders[folder]

            time.sleep(5)  # Перевіряємо кожні 5 секунд

        except Exception as e:
            print(f"Помилка під час синхронізації: {e}")
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

    stop_monitoring_flag = False
    monitoring_thread = threading.Thread(
        target=sync_with_firebase,
        args=(directory_path,),
        daemon=True
    )
    monitoring_thread.start()
    return True

def stop_monitoring():
    global stop_monitoring_flag
    stop_monitoring_flag = True
    if monitoring_thread and monitoring_thread.is_alive():
        monitoring_thread.join(timeout=2)
    messagebox.showinfo("Інформація", "Моніторинг зупинено")

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Folder-Firebase Sync")

        self.config = load_config()

        # Змінні для полів вводу
        self.firebase_url = StringVar(value=self.config.get("firebase_url", ""))
        self.firebase_key_path = StringVar(value=self.config.get("firebase_key_path", ""))
        self.directory_path = StringVar(value=self.config.get("directory_path", ""))

        # Елементи інтерфейсу
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
        # Зберігаємо налаштування
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