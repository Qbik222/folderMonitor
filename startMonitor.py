import os
import threading
import time
import json
import re
from tkinter import Tk, Label, Entry, Button, StringVar, messagebox, Toplevel, Text, Scrollbar, Frame, ttk
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime

CONFIG_FILE = "config.json"
firebase_app = None
database_ref = None
monitoring_threads = {}
stop_monitoring_flags = {}
log_windows = {}
update_intervals = {}
tracked_files = {}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as file:
            return json.load(file)
    return {"firebase_url": "", "firebase_key_path": ""}

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
        log_message("Global", "Firebase ініціалізовано успішно!")
        return True
    except Exception as e:
        log_message("Global", f"Помилка ініціалізації Firebase: {e}", error=True)
        return False

def clear_firebase_data(window_name):
    try:
        if database_ref:
            db.reference(f"/frequency/{window_name}").delete()
            log_message("Global", f"Дані вікна {window_name} очищено успішно!")
            return True
    except Exception as e:
        log_message("Global", f"Помилка при очищенні даних для {window_name}: {e}", error=True)
        return False

def log_message(window_name, message, error=False):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_text = f"[{timestamp}] {message}"

    if window_name in log_windows and log_windows[window_name].winfo_exists():
        log_windows[window_name].text.configure(state='normal')
        tag = "error" if error else "info"
        log_windows[window_name].text.insert('end', log_text + "\n", tag)
        log_windows[window_name].text.configure(state='disabled')
        log_windows[window_name].text.see('end')

    print(f"[{window_name}] {log_text}")

def create_log_window(window_name):
    if window_name not in log_windows or not log_windows[window_name].winfo_exists():
        log_windows[window_name] = Toplevel()
        log_windows[window_name].title(f"Лог моніторингу: {window_name}")
        log_windows[window_name].geometry("800x400")

        scrollbar = Scrollbar(log_windows[window_name])
        scrollbar.pack(side="right", fill="y")

        log_windows[window_name].text = Text(log_windows[window_name], wrap="word", yscrollcommand=scrollbar.set)
        log_windows[window_name].text.pack(expand=True, fill="both")

        log_windows[window_name].text.tag_config("info", foreground="green")
        log_windows[window_name].text.tag_config("error", foreground="red")
        log_windows[window_name].text.configure(state='disabled')

        scrollbar.config(command=log_windows[window_name].text.yview)

def extract_frequency_from_file(file_path):
    try:
        filename = os.path.splitext(os.path.basename(file_path))[0]

        # Пошук у форматі XXX.XXX або XXX,XXX
        match = re.search(r'(\d{3})[.,](\d{3})', filename)
        if match:
            return f"{match.group(1)}.{match.group(2)}"

        # Пошук числа з 3 до 6 цифр
        match = re.search(r'\d{3,6}', filename)
        if match:
            number = match.group(0)
            if len(number) == 3:
                return f"{number}.000"
            else:
                return f"{number[:3]}.{number[3:]}"

    except Exception as e:
        log_message("Global", f"Помилка аналізу назви файлу {file_path}: {e}", error=True)

    return None

def scan_directory_recursive(directory, existing_files=None):
    frequencies = {}
    if existing_files is None:
        existing_files = set()
    
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            if file_path in existing_files:
                continue
                
            frequency = extract_frequency_from_file(file_path)
            if frequency:
                frequencies[file_path] = {
                    'frequency': frequency,
                    'last_modified': os.path.getmtime(file_path)
                }
    return frequencies

def sync_with_firebase(window_name, directory_path):
    global stop_monitoring_flags, update_intervals, tracked_files

    if window_name not in tracked_files:
        tracked_files[window_name] = {}

    # При першому запуску отримуємо список всіх існуючих файлів
    initial_files = set()
    for root, dirs, files in os.walk(directory_path):
        for file in files:
            file_path = os.path.join(root, file)
            initial_files.add(file_path)

    log_message(window_name, f"Початок моніторингу папки: {directory_path}")
    log_message(window_name, f"Ігнорується {len(initial_files)} існуючих файлів")
    log_message(window_name, f"Поточний інтервал оновлення: {update_intervals.get(window_name, 5)} сек")

    while not stop_monitoring_flags.get(window_name, False):
        try:
            # Скануємо тільки нові файли (не існуючі на момент запуску)
            current_files = scan_directory_recursive(directory_path, initial_files)
            log_message(window_name, f"Знайдено {len(current_files)} нових файлів з частотами у директорії")

            # Видалені файли
            deleted_files = set(tracked_files[window_name].keys()) - set(current_files.keys())
            for file_path in deleted_files:
                if file_path in tracked_files[window_name]:
                    db.reference(f"/frequency/{window_name}/{tracked_files[window_name][file_path]['firebase_key']}").delete()
                    log_message(window_name, f"Видалено частоту: {tracked_files[window_name][file_path]['frequency']} (файл: {file_path})")
                    del tracked_files[window_name][file_path]

            # Нові або змінені файли
            for file_path, file_data in current_files.items():
                if 'frequency' not in file_data:
                    log_message(window_name, f"Пропущено файл без частоти: {file_path}", error=True)
                    continue

                if (file_path not in tracked_files[window_name] or
                    file_data['last_modified'] > tracked_files[window_name][file_path]['last_modified']):

                    if file_path not in tracked_files[window_name]:
                        new_ref = db.reference(f"/frequency/{window_name}").push()
                        tracked_files[window_name][file_path] = {
                            'firebase_key': new_ref.key,
                            'last_modified': file_data['last_modified'],
                            'frequency': file_data['frequency']
                        }
                    else:
                        new_ref = db.reference(f"/frequency/{window_name}/{tracked_files[window_name][file_path]['firebase_key']}")
                        tracked_files[window_name][file_path]['last_modified'] = file_data['last_modified']
                        tracked_files[window_name][file_path]['frequency'] = file_data['frequency']

                    data = {
                        'name': file_data['frequency'],
                        'file_path': file_path,
                        'timestamp': time.time(),
                        'status': 'active',
                        'last_modified': file_data['last_modified'],
                        'window_name': window_name
                    }
                    new_ref.set(data)

                    log_message(window_name, f"Оновлено частоту: {file_data['frequency']} (файл: {file_path})")

            time.sleep(update_intervals.get(window_name, 5))

        except Exception as e:
            log_message(window_name, f"Помилка синхронізації: {e}", error=True)
            time.sleep(update_intervals.get(window_name, 5))

def start_monitoring_window(window_name, directory_path):
    global monitoring_threads, stop_monitoring_flags
    
    if not directory_path or not os.path.isdir(directory_path):
        messagebox.showerror("Помилка", "Вкажіть коректний шлях до папки!")
        return False

    if window_name in monitoring_threads and monitoring_threads[window_name].is_alive():
        messagebox.showerror("Помилка", f"Моніторинг для вікна {window_name} вже запущений!")
        return False

    stop_monitoring_flags[window_name] = False
    monitoring_threads[window_name] = threading.Thread(
        target=sync_with_firebase,
        args=(window_name, directory_path),
        daemon=True
    )
    monitoring_threads[window_name].start()
    log_message(window_name, "Моніторинг запущено")
    return True

def stop_monitoring_window(window_name):
    global stop_monitoring_flags
    stop_monitoring_flags[window_name] = True
    if window_name in monitoring_threads and monitoring_threads[window_name].is_alive():
        monitoring_threads[window_name].join(timeout=2)
    log_message(window_name, "Моніторинг зупинено")
    messagebox.showinfo("Інформація", f"Моніторинг для вікна {window_name} зупинено")

def toggle_log_window(window_name):
    if window_name in log_windows and log_windows[window_name].winfo_exists():
        log_windows[window_name].destroy()
        del log_windows[window_name]
    else:
        create_log_window(window_name)

class MonitoringWindow:
    def __init__(self, root, main_app, window_name):
        self.root = root
        self.main_app = main_app
        self.window_name = window_name
        
        self.window = Toplevel(root)
        self.window.title(f"Моніторинг: {window_name}")
        
        self.directory_path = StringVar()
        self.update_interval = StringVar(value="5")
        self.monitoring_status = StringVar(value="Моніторинг вимкнено")
        self.status_color = "red"
        
        Label(self.window, text="Назва вікна:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        Label(self.window, text=window_name).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        
        self.status_label = Label(self.window, textvariable=self.monitoring_status, fg=self.status_color)
        self.status_label.grid(row=0, column=2, sticky="e", padx=10, pady=5)
        
        Label(self.window, text="Папка для моніторингу:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        Entry(self.window, textvariable=self.directory_path, width=50).grid(row=1, column=1, padx=5, pady=5)
        Button(self.window, text="Огляд...", command=self.browse_directory).grid(row=1, column=2, padx=5, pady=5)
        
        Label(self.window, text="Інтервал оновлення (сек):").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        Entry(self.window, textvariable=self.update_interval, width=10).grid(row=2, column=1, sticky="w", padx=5, pady=5)
        
        Button(self.window, text="Почати моніторинг", command=self.start_monitoring).grid(row=3, column=1, pady=10)
        Button(self.window, text="Зупинити моніторинг", command=self.stop_monitoring).grid(row=4, column=1, pady=5)
        Button(self.window, text="Показати/сховати лог", command=self.toggle_log).grid(row=5, column=1, pady=5)
        
        if window_name in self.main_app.windows_data:
            data = self.main_app.windows_data[window_name]
            self.directory_path.set(data.get("directory_path", ""))
            self.update_interval.set(data.get("update_interval", "5"))
    
    def browse_directory(self):
        from tkinter import filedialog
        directory = filedialog.askdirectory(title="Виберіть папку для моніторингу")
        if directory:
            self.directory_path.set(directory)
    
    def update_status(self, is_running):
        if is_running:
            self.monitoring_status.set("Моніторинг запущено")
            self.status_color = "green"
        else:
            self.monitoring_status.set("Моніторинг вимкнено")
            self.status_color = "red"
        self.status_label.config(fg=self.status_color)
    
    def start_monitoring(self):
        try:
            interval = int(self.update_interval.get())
            if interval < 1:
                messagebox.showerror("Помилка", "Інтервал оновлення повинен бути не менше 1 секунди")
                return False
            update_intervals[self.window_name] = interval
        except ValueError:
            messagebox.showerror("Помилка", "Введіть коректне число для інтервалу оновлення")
            return False

        self.main_app.windows_data[self.window_name] = {
            "directory_path": self.directory_path.get(),
            "update_interval": self.update_interval.get()
        }
        self.main_app.save_windows_data()

        if start_monitoring_window(
            self.window_name,
            self.directory_path.get()
        ):
            self.update_status(True)
            messagebox.showinfo("Успіх", f"Моніторинг для вікна {self.window_name} запущено!")
            return True
        return False
    
    def stop_monitoring(self):
        stop_monitoring_window(self.window_name)
        self.update_status(False)
    
    def toggle_log(self):
        toggle_log_window(self.window_name)

class MainApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Folder-Firebase Sync Manager")

        self.config = load_config()
        self.windows_data = self.config.get("windows", {})
        
        self.firebase_url = StringVar(value=self.config.get("firebase_url", ""))
        self.firebase_key_path = StringVar(value=self.config.get("firebase_key_path", ""))
        self.new_window_name = StringVar()
        
        Label(root, text="Firebase URL:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        Entry(root, textvariable=self.firebase_url, width=50).grid(row=0, column=1, padx=5, pady=5)

        Label(root, text="Шлях до ключа Firebase:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        Entry(root, textvariable=self.firebase_key_path, width=50).grid(row=1, column=1, padx=5, pady=5)
        Button(root, text="Огляд...", command=self.browse_key_file).grid(row=1, column=2, padx=5, pady=5)
        
        ttk.Separator(root, orient="horizontal").grid(row=2, column=0, columnspan=3, sticky="ew", pady=10)
        
        Label(root, text="Назва нового вікна моніторингу:").grid(row=3, column=0, sticky="w", padx=10, pady=5)
        Entry(root, textvariable=self.new_window_name, width=30).grid(row=3, column=1, sticky="w", padx=5, pady=5)
        Button(root, text="Створити вікно", command=self.create_monitoring_window).grid(row=3, column=2, padx=5, pady=5)
        
        self.windows_frame = Frame(root)
        self.windows_frame.grid(row=4, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")
        
        self.update_windows_list()
        
        root.grid_rowconfigure(4, weight=1)
        root.grid_columnconfigure(1, weight=1)
        
        create_log_window("Global")
    
    def browse_key_file(self):
        from tkinter import filedialog
        key_path = filedialog.askopenfilename(title="Виберіть файл ключа Firebase")
        if key_path:
            self.firebase_key_path.set(key_path)
            self.save_config()
    
    def save_config(self):
        self.config.update({
            "firebase_url": self.firebase_url.get(),
            "firebase_key_path": self.firebase_key_path.get(),
            "windows": self.windows_data
        })
        save_config(self.config)
    
    def save_windows_data(self):
        self.config["windows"] = self.windows_data
        save_config(self.config)
    
    def create_monitoring_window(self):
        window_name = self.new_window_name.get().strip()
        if not window_name:
            messagebox.showerror("Помилка", "Введіть назву вікна!")
            return
        
        if not self.firebase_url.get() or not self.firebase_key_path.get():
            messagebox.showerror("Помилка", "Спочатку вкажіть Firebase URL та шлях до ключа!")
            return
        
        if not initialize_firebase(self.firebase_url.get(), self.firebase_key_path.get()):
            return
        
        if window_name not in self.windows_data:
            self.windows_data[window_name] = {
                "directory_path": "",
                "update_interval": "5"
            }
            self.save_windows_data()
        
        MonitoringWindow(self.root, self, window_name)
        self.new_window_name.set("")
        self.update_windows_list()
    
    def update_windows_list(self):
        for widget in self.windows_frame.winfo_children():
            widget.destroy()

        if not self.windows_data:
            Label(self.windows_frame, text="Немає створених вікон моніторингу").pack(pady=10)
            return

        Label(self.windows_frame, text="Список вікон моніторингу:").pack(anchor="w")

        for window_name in sorted(self.windows_data.keys()):
            frame = Frame(self.windows_frame)
            frame.pack(fill="x", pady=2)

            status_var = StringVar()
            is_active = window_name in monitoring_threads and monitoring_threads[window_name].is_alive()
            status_var.set("🟢 Активний" if is_active else "🔴 Вимкнений")
            
            Label(frame, text=window_name, width=20).pack(side="left")
            status_label = Label(frame, textvariable=status_var, 
                               fg="green" if is_active else "red")
            status_label.pack(side="left", padx=5)
            
            Button(frame, text="Відкрити", 
                  command=lambda wn=window_name: self.open_monitoring_window(wn)).pack(side="left", padx=5)
            Button(frame, text="Видалити", 
                  command=lambda wn=window_name: self.delete_window(wn)).pack(side="left", padx=5)

            self.start_status_check(window_name, status_var, status_label)

    def start_status_check(self, window_name, status_var, status_label):
        def check_status():
            if window_name not in self.windows_data:
                return
                
            is_active = window_name in monitoring_threads and monitoring_threads[window_name].is_alive()
            status_var.set("🟢 Активний" if is_active else "🔴 Вимкнений")
            status_label.config(fg="green" if is_active else "red")
            
            self.root.after(1000, check_status)
        
        check_status()

    def open_monitoring_window(self, window_name):
        if not self.firebase_url.get() or not self.firebase_key_path.get():
            messagebox.showerror("Помилка", "Спочатку вкажіть Firebase URL та шлях до ключа!")
            return
        
        if not initialize_firebase(self.firebase_url.get(), self.firebase_key_path.get()):
            return
        
        MonitoringWindow(self.root, self, window_name)
    
    def delete_window(self, window_name):
        if messagebox.askyesno("Підтвердження", f"Видалити вікно {window_name}? Дані моніторингу буде втрачено."):
            if window_name in monitoring_threads and monitoring_threads[window_name].is_alive():
                stop_monitoring_window(window_name)
            
            if window_name in log_windows and log_windows[window_name].winfo_exists():
                log_windows[window_name].destroy()
                del log_windows[window_name]
            
            if window_name in tracked_files:
                del tracked_files[window_name]
            
            if window_name in update_intervals:
                del update_intervals[window_name]
            
            if window_name in monitoring_threads:
                del monitoring_threads[window_name]
            
            if window_name in stop_monitoring_flags:
                del stop_monitoring_flags[window_name]
            
            if window_name in self.windows_data:
                del self.windows_data[window_name]
                self.save_windows_data()
            
            self.update_windows_list()
            
            clear_firebase_data(window_name)
            
            log_message("Global", f"Вікно {window_name} повністю видалено")

if __name__ == "__main__":
    root = Tk()
    app = MainApp(root)
    root.mainloop()