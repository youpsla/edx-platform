from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from confluent_kafka import SerializingProducer
from lms.djangoapps.arch_experiments.event_bus.course_events import (
    COURSE_EVENT_KEY_SERIALIZER,
    COURSE_EVENT_VALUE_SERIALIZER,
    Course,
    CourseEventKey,
    CourseEnrollmentEventValue,
)

class Command(BaseCommand):
    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        def acked(err, msg):
            if err is not None:
                print(f"Failed to deliver message: {msg}: {err}")
            else:
                print(f"Message produced: {msg}")

        producer_settings = dict(settings.KAFKA_PRODUCER_CONF_BASE)
        producer_settings.update({'key.serializer': COURSE_EVENT_KEY_SERIALIZER,
                                  'value.serializer': COURSE_EVENT_VALUE_SERIALIZER}

        )
        producer = SerializingProducer(producer_settings)
        print("Producing test messages")

        for i in range(10):
            course = Course(f"edX+{i}", f"Introduction to the number {i}", "edX")
            event_key = CourseEventKey(course.course_key)
            event_value = CourseEnrollmentEventValue(course, '12345', i%2 == 0)
            producer.produce(
                topic="course_events",
                key=event_key,
                value=event_value,
                on_delivery=acked,
            )
        producer.flush()

