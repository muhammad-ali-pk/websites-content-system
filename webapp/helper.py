from webapp.models import JiraTask, User, Project, Webpage, db, get_or_create
from enum import Enum


class RequestType(Enum):
    COPY_UPDATE = 0
    PAGE_REFRESH = 1
    NEW_WEBPAGE = 2


def get_or_create_user_id(user):
    # If user does not exist, create a new user in the "users" table
    user_hrc_id = user.get("id")
    user_exists = User.query.filter_by(hrc_id=user_hrc_id).first()
    if not user_exists:
        user_exists, _ = get_or_create(
            db.session,
            User,
            name=user.get("name"),
            email=user.get("email"),
            team=user.get("team"),
            department=user.get("department"),
            job_title=user.get("jobTitle"),
            hrc_id=user_hrc_id,
        )

    return user_exists.id


def create_jira_task(app, body):
    """
    Create a new issue on jira and add a record to the db
    """
    # TODO: If an epic already exists for this request, add subtasks to it.

    # Get the webpage
    webpage_id = body["webpage_id"]
    webpage = Webpage.query.filter_by(id=webpage_id).first()
    if not webpage:
        raise Exception(f"Webpage with ID {webpage_id} not found")

    # Determine summary message in case it's not provided by a user
    summary = body.get("summary")
    if len(summary) == 0:
        if body["type"] == RequestType.COPY_UPDATE.value:
            summary = f"Copy update {webpage.name}"
        elif body["type"] == RequestType.PAGE_REFRESH.value:
            summary = f"Page refresh for {webpage.name}"
        elif body["type"] == RequestType.NEW_WEBPAGE.value:
            summary = f"New webpage for {webpage.name}"
        else:
            summary = ""

    jira = app.config["JIRA"]
    issue = jira.create_issue(
        due_date=body["due_date"],
        reporter_id=body["reporter_id"],
        request_type=body["type"],
        description=body["description"],
        summary=summary,
    )

    # Create jira task in the database
    get_or_create(
        db.session,
        JiraTask,
        jira_id=issue["key"],
        webpage_id=body["webpage_id"],
        user_id=body["reporter_id"],
        summary=summary,
    )


def get_project_id(project_name):
    project = Project.query.filter_by(name=project_name).first()
    return project.id if project else None


def get_webpage_id(name, project_id):
    webpage = Webpage.query.filter_by(name=name, project_id=project_id).first()
    return webpage.id if webpage else None


def convert_webpage_to_dict(webpage, owner, project):
    # Preload relationships
    webpage.reviewers
    webpage.jira_tasks
    webpage.owner
    webpage.project

    webpage_dict = webpage.__dict__.copy()

    # Remove unnecessary fields
    webpage_dict.pop("_sa_instance_state", None)
    webpage_dict.pop("owner_id", None)
    webpage_dict.pop("project_id", None)
    owner = webpage_dict.pop("owner", None)
    project = webpage_dict.pop("project", None)
    reviewers = webpage_dict.pop("reviewers", None)
    jira_tasks = webpage_dict.pop("jira_tasks", None)

    # Serialize owner fields
    if owner:
        owner_dict = owner.__dict__.copy()
        owner_dict["created_at"] = owner.created_at.isoformat()
        owner_dict["updated_at"] = owner.updated_at.isoformat()
        if owner_dict["_sa_instance_state"]:
            owner_dict.pop("_sa_instance_state", None)
    else:
        owner_dict = {}

    # Serialize project fields
    if project:
        project_dict = project.__dict__.copy()
        project_dict["created_at"] = project.created_at.isoformat()
        project_dict["updated_at"] = project.updated_at.isoformat()
        if project_dict["_sa_instance_state"]:
            project_dict.pop("_sa_instance_state", None)
    else:
        project_dict = {}

    # Serialize reviewers fields
    if reviewers:
        reviewers_list = []
        for reviewer in reviewers:
            reviewer_dict = reviewer.__dict__.copy()
            reviewer_dict.pop("_sa_instance_state", None)
            reviewer_dict["created_at"] = reviewer.created_at.isoformat()
            reviewer_dict["updated_at"] = reviewer.updated_at.isoformat()
            # Expand the user object
            reviewer_user_dict = reviewer.user.__dict__.copy()
            reviewer_user_dict.pop("created_at")
            reviewer_user_dict.pop("updated_at")
            if reviewer_user_dict["_sa_instance_state"]:
                reviewer_user_dict.pop("_sa_instance_state", None)
            reviewer_dict = {**reviewer_dict, **reviewer_user_dict}
            reviewers_list.append(reviewer_dict)
    else:
        reviewers_list = []

    # Serialize jira_tasks fields
    if jira_tasks:
        jira_tasks_list = []
        for jira_task in jira_tasks:
            jira_task_dict = jira_task.__dict__.copy()
            jira_task_dict.pop("_sa_instance_state", None)
            jira_task_dict["created_at"] = jira_task.created_at.isoformat()
            jira_task_dict["updated_at"] = jira_task.updated_at.isoformat()
            # Expand the user object
            jira_task_user_dict = jira_task.user.__dict__.copy()
            jira_task_user_dict.pop("created_at")
            jira_task_user_dict.pop("updated_at")
            if jira_task_user_dict["_sa_instance_state"]:
                jira_task_user_dict.pop("_sa_instance_state", None)
            jira_task_dict = {**jira_task_dict, **jira_task_user_dict}
            jira_tasks_list.append(jira_task_dict)
    else:
        jira_tasks_list = []

    # Serialize object fields
    webpage_dict["status"] = webpage.status.value
    webpage_dict["created_at"] = webpage.created_at.isoformat()
    webpage_dict["updated_at"] = webpage.updated_at.isoformat()
    webpage_dict["owner"] = owner_dict
    webpage_dict["project"] = project_dict
    webpage_dict["reviewers"] = reviewers_list
    webpage_dict["jira_tasks"] = jira_tasks_list

    return webpage_dict


def create_copy_doc(app, webpage):
    client = app.config["gdrive"]
    task = client.create_copydoc_from_template(webpage)
    return f"https://docs.google.com/document/d/{task['id']}" if task else None


# recursively build tree from webpages table rows
def build_tree(session, page, webpages):
    child_pages = list(filter(lambda p: p.parent_id == page["id"], webpages))
    for child_page in child_pages:
        project = get_or_create(session, Project, id=child_page.project_id)
        owner = get_or_create(session, User, id=child_page.owner_id)
        new_child = convert_webpage_to_dict(child_page, owner, project)
        new_child["children"] = []
        page["children"].append(new_child)
        build_tree(session, new_child, webpages)


def get_tree_struct(session, webpages):
    # sort webpages list by their name
    webpages_list = sorted(
        list(webpages), key=lambda p: p.name.rsplit("/", 1)[-1]
    )
    parent_page = next(
        filter(lambda p: p.parent_id is None, webpages_list), None
    )

    if parent_page:
        project = get_or_create(session, Project, id=parent_page.project_id)
        owner = get_or_create(session, User, id=parent_page.owner_id)
        tree = convert_webpage_to_dict(parent_page, owner, project)
        tree["children"] = []
        build_tree(session, tree, webpages_list)
        return tree

    return None
