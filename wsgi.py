# -*- coding: utf-8 -*-

"""An example WSGI application for running a job on a file.

The message queue, the Python web application, and the celery worker all have to be running at the same time.

Run the Broker
--------------
This example uses `RabbitMQ <https://www.rabbitmq.com/>`_, or see the ``celery`` documentation for
more `choices and installation instructions
<http://docs.celeryproject.org/en/latest/getting-started/first-steps-with-celery.html#choosing-a-broker>`_.

Run the Application
----------------
The application can be quickly run with the Flask debugging server, however, this should not be used in production.

.. code-block:: bash

    $ python -m wsgi.py

After installing `gunicorn <http://gunicorn.org/>`_ with ``pip install gunicorn``, it can be run with a more
production-ready server:

.. code-block:: bash

    $ gunicorn -b 0.0.0.0:5000 wsgi:app --log-level=INFO

Alternatively `uWSGI <https://uwsgi-docs.readthedocs.io/en/latest/>`_, Apache, or other
`deployment options <http://flask.pocoo.org/docs/1.0/deploying/#deployment>`_.

Run the Celery Worker
---------------------
.. code-block:: bash

    $ celery worker -A wsgi.celery -l INFO
"""

import logging
import os
import random
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Mapping, Tuple

from celery import Celery
from celery.result import AsyncResult
from celery.utils.log import get_task_logger
from flask import Flask, Markup, flash, jsonify, redirect, render_template, url_for
from flask_bootstrap import Bootstrap
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms.fields import SubmitField
from wtforms.validators import DataRequired

###########
# Logging #
###########

# This logger goes to the WSGI server
logger = logging.getLogger(__name__)

# This logger goes to the celery worker
celery_logger = get_task_logger(__name__)

#################
# Configuration #
#################

#: The name of the configuration option for the Celery broker URL.
CELERY_BROKER_URL = 'CELERY_BROKER_URL'

#: The name of the configuration option for the Celery result backend.
CELERY_RESULT_BACKEND = 'CELERY_RESULT_BACKEND'

#: The name of the configuration option for Flask security purposes.
SECRET_KEY = 'SECRET_KEY'

#: The configuration dictionary for flask and celery. In your application, this
#: might be loaded from the environment or elsewhere.
config = {

    # The broker is the address of the message queue that mediates
    # communication between the Flask app and the worker In this
    # example, RabbitMQ is used over AMPQ protocol. As an example,
    # use:  'amqp://localhost'
    # See: http://docs.celeryproject.org/en/latest/getting-started/first-steps-with-celery.html#choosing-a-broker
    CELERY_BROKER_URL: os.environ[CELERY_BROKER_URL],

    # The result backend stores the results of tasks. As an example,
    # SQLAlchemy can be used with SQLite like: 'db+sqlite:///results.sqlite'
    # See: http://celery.readthedocs.io/en/latest/userguide/configuration.html#task-result-backend-settings
    CELERY_RESULT_BACKEND: os.environ[CELERY_RESULT_BACKEND],

    # Set a random key for CSRF (for Flask-WTF)
    SECRET_KEY: os.environ.get(SECRET_KEY, os.urandom(8))
}


#########
# Forms #
#########

class MyForm(FlaskForm):
    """A form for uploading a file."""

    file = FileField('My File', validators=[DataRequired()])
    submit = SubmitField('Upload')


def handle_form(form: MyForm) -> Tuple[str,]:
    """Prepare a file upload for for the task queue.

    :param form: The FlaskForm to handle
    :return: A 1-tuple pass as arguments to the task queue in which
             the first element is the contents as a string.
    """
    contents = form.file.data.stream.read()
    contents = urlsafe_b64encode(contents).decode("utf-8")
    return contents,


#############
# Flask App #
#############

# Create the Flask application (needs same name as python file so tasks can
# get found properly. Use __name__ otherwise)
app = Flask('wsgi')

# Update configuration. Might want to get from a config file or environment
# for different deployments
app.config.update(config)

# Add the Flask-Bootstrap extension
Bootstrap(app)

##############
# Celery App #
##############

# Add Celery magic
celery = Celery(
    app.import_name,
    broker=app.config[CELERY_BROKER_URL],
    backend=app.config[CELERY_RESULT_BACKEND],
)


# Define the task using the celery.task annotation
@celery.task
def my_task(contents: str) -> Mapping:
    """Take some simple statistics over a file.

    :param contents: The contents of a file (base64 urlencoded)
    """
    try:
        contents = urlsafe_b64decode(contents.encode('utf-8')).decode('utf-8')
    except UnicodeDecodeError:
        rv = 'failed to decode.'
    else:
        rv = {
            'lines': contents.count('\n'),
            'characters': len(contents),
        }

    # for this example, sleep a trivial amount of time to show what happens
    # when a job is running
    time.sleep(random.randint(5, 10))

    return rv


################
# Flask Routes #
################


@app.route('/', methods=['GET', 'POST'])
def home():
    """Serve the home page."""
    form = MyForm()

    if not form.validate_on_submit():
        return render_template('index.html', form=form)

    args = handle_form(form)

    # send the task to the queue (will happen asynchronously)
    task = my_task.delay(*args)

    url = url_for('results', task=task.task_id)
    flash(Markup(f'Queued task <code><a href="{url}">{task}</a></code>.'))

    return render_template('index.html', form=form)


@app.route('/check/<task>', methods=['GET'])
def check(task: str):
    """Check the given task.

    :param task: The UUID of a task.
    """
    task = AsyncResult(task, app=celery)

    if not task.successful():
        return jsonify(
            task_id=task.task_id,
            status=task.status,
            result=task.result,
        )

    return jsonify(
        task_id=task.task_id,
        status=task.status
    )


@app.route('/results/<task>', methods=['GET'])
def results(task: str):
    """Check the given task.

    :param task: The UUID of a task.
    """
    task = AsyncResult(task, app=celery)

    if not task.successful():
        url = url_for('results', task=task.task_id)
        markup = Markup(f'Task <code><a href="{url}">{task}</a></code> is not yet complete.')
        flash(markup, category='warning')
        return redirect(url_for('home'))

    return render_template('results.html', task=task)


########
# Main #
########

if __name__ == '__main__':
    app.run(debug=True)
