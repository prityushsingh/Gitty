# 🧩 Gitty – A Simple Git Implementation in Python

> **Built for Learning Purpose**  
> A minimal Git clone written in pure Python to understand how version control systems work internally.

---

## 📖 What is Gitty?

**Gitty** is a lightweight Python project that re-implements the **core features of Git** — like commits, branching, and staging — from scratch.  
It’s designed purely for **educational purposes**, helping you learn how Git stores and manages data behind the scenes.

---

## ⚙️ Core Components

### 1. GitObject Class
- Base class for all Gitty objects (Blob, Tree, Commit).
- Handles serialization and hashing (SHA-1).
- Compresses and stores data under `.gitty/objects/`.

### 2. Blob Objects
- Represent file contents.
- Store the raw bytes of each file.

### 3. Tree Objects
- Represent directories.
- Contain references to Blobs (files) and other Trees (subdirectories).

### 4. Commit Objects
- Record a repository snapshot.
- Contain commit metadata (message, author, parent commit).
- Link to the corresponding Tree.

### 5. Repository Structure
- Manages `.gitty` directory, object database, references, and staging index.
- Implements core Git commands.

---

## 🚀 Features

- Repository initialization (`init`)
- Staging files (`add`)
- Creating commits (`commit`)
- Managing branches (`branch`, `checkout`)
- Viewing logs (`log`)
- Checking repository state (`status`)
- Object storage using hashing & compression

---

## 🧰 Installation

### Prerequisites
- Python **3.8+**
- No third-party dependencies (uses standard library only)

### Setup

```bash
https://github.com/prityushsingh/Gitty.git
cd Gitty
python main.py --help
