## Local Git Workflow (Quick Guide)

After initializing Git (`git init`) and making your first commit, here's the basic workflow for tracking changes in your project locally:

1.  **Make Changes:**
    *   Edit existing files (`.py`, `.md`, etc.).
    *   Create new files or directories.
    *   Delete unnecessary files.

2.  **Check Status (`git status`):**
    *   At any point, open your terminal in the project directory and run:
        ```bash
        git status
        ```
    *   This command shows:
        *   `Changes not staged for commit:` (Files that have been **modified** since the last commit).
        *   `Untracked files:` (New files that Git doesn't know about yet).
        *   `Changes to be committed:` (Files you have **added** to the staging area, ready for the next commit).
        *   `nothing to commit, working tree clean` (If the current file state matches the last commit).

3.  **Stage Changes (`git add`):**
    *   To prepare modified or new files for saving (committing), add them to the "staging area" (index).
    *   **Stage a specific file:**
        ```bash
        git add filename.py
        git add folder/another_file.txt
        ```
    *   **Stage ALL modified and new files** (respecting `.gitignore` rules):
        ```bash
        git add .
        ```
        *(Use `git add .` carefully. Ensure your `.gitignore` is comprehensive to avoid staging temporary or unwanted files).*
    *   Run `git status` again after `git add` to see the files moved to the "Changes to be committed" section.

4.  **Save Changes (Commit) (`git commit`):**
    *   Once you have staged all the desired changes, create a "snapshot" of the project's state by making a commit.
    *   Run the command with a meaningful message using the `-m` flag:
        ```bash
        git commit -m "A brief description of the changes made"
        ```
    *   **Examples of good commit messages:**
        *   `git commit -m "Fix quest history pagination bug"`
        *   `git commit -m "Add !ban command"`
        *   `git commit -m "Update README with new setup instructions"`
        *   `git commit -m "Refactor API request logic in snag_cog"`
    *   Try to make commits for logically complete units of work.

5.  **View History (`git log`):**
    *   To see the commit history:
        ```bash
        git log
        ```
        *(Press `q` to exit the log viewer).*
    *   For a more concise log:
        ```bash
        git log --oneline
        ```
    *   For a log with a branch graph (useful when working with branches):
        ```bash
        git log --graph --oneline --all
        ```

**The Basic Loop:**

**Edit -> `git status` -> `git add` -> `git status` -> `git commit` -> Repeat**

**Other Useful Local Commands:**

*   **`git diff`:** Shows the differences between your current working files and the last commit (for changes *not yet* staged with `git add`).
*   **`git diff --staged`:** Shows the differences between the staged files (after `git add`) and the last commit.
*   **`git checkout -- <filename>`:** **CAUTION!** Discards *all* unstaged changes in the specified file, reverting it to the state of the last commit. Changes will be lost!
*   **`git reset HEAD <filename>`:** Unstages a file (reverses `git add`), but keeps the actual changes in the working file.
*   **`git branch <branch-name>`:** Creates a new branch.
*   **`git checkout <branch-name>`:** Switches to a different branch.
*   **`git merge <branch-name>`:** Merges the specified branch into your current branch.

Using Git locally is a powerful way to organize your work and prevent data loss.